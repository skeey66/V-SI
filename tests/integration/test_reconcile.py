"""리컨실리에이션 루프 통합 테스트.

전제: `docker compose up -d --build`로 스택이 떠 있어야 한다.

여기서 고정하는 것은 하나다 — **오케스트레이터는 죽어도 된다.** 크래시 복구가
특수 경로가 아니라 평시에도 도는 수렴 루프이므로, 임의 시점에 SIGKILL을 맞아도
재기동만으로 중단 없이 끝까지 간다.

복구 케이스 네 가지를 각각 시험한다:

1. 푸시가 영영 오지 않는 `working` 행 (executor 크래시) — `qa_fails_twice`
2. `remediating` + 올라간 revision + 그 revision의 Task 0개 (`remediate` 중간 크래시)
3. `submitted` + `a2a_task_id` NULL (`submit()` 자체가 터진 경우)
4. 실행 중 오케스트레이터 SIGKILL

2·3번은 손으로 그 상태를 DB에 만들어 놓고 **오케스트레이터 컨테이너 안에서 도는
진짜 리컨실러**가 스스로 수렴시키는지 본다(테스트 프로세스가 리컨실러를 직접
호출하지 않는다 — 배포된 그대로를 시험한다).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.idempotency import idempotency_key
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.workflow import TERMINAL, RequirementState
from tests.integration.harness import (
    ScenarioResult,
    db_session,
    kill_orchestrator,
    purge,
    reset_agents,
    restart_orchestrator,
    run_scenario,
    wait_for,
)

ALL_PASS = "scenarios/all_pass.yaml"
ALL_PASS_SLOW = "scenarios/all_pass_slow.yaml"
QA_FAILS_TWICE = "scenarios/qa_fails_twice.yaml"
VERDICTS_FAIL_TWICE = "scenarios/verdicts_fail_twice.yaml"

RECOVERY_TIMEOUT_S = 90.0

#: kill 지점에 도달할 때까지의 대기. `run_scenario` 가 시작하며 `reset_agents` 로
#: **에이전트 컨테이너 4개를 재생성**하므로, 이 예산에는 요구사항 실행뿐 아니라
#: 그 재생성 시간이 통째로 들어간다. 원래 60초였는데 SP2 가 `workspace` 를 더해
#: 스택이 11개 서비스가 되면서 전체 스위트 부하에서 간헐적으로 넘겼다(실측).
#: 하네스의 `BOOT_TIMEOUT_S`(120초)가 "kill·재기동을 반복하면 도커가 느려진다
#: (실측 60초 초과)"를 근거로 잡힌 값이므로 같은 기준에 맞춘다.
KILL_POINT_TIMEOUT_S = 120.0


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


def _old(seconds: float = 600.0) -> datetime:
    """리컨실러의 staleness 기준을 확실히 넘긴 과거 시각."""
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# --------------------------------------------------------------- 케이스 1


async def test_qa_fails_twice_reaches_accepted() -> None:
    """dev가 2회차 호출에서 크래시하는 시나리오가 끝까지 간다.

    이것이 리컨실러가 동작한다는 가장 강한 증거다. SDK는 executor 오류 경로에서
    푸시를 보내지 않으므로(Task 11 실측) 크래시한 Task는 오케스트레이터에게
    **아무 신호도 남기지 않는다**. 오래 머문 행을 폴링해 권위 있게 읽는 루프가
    없으면 이 시나리오는 revision 2의 dev에서 영원히 멈춘다.
    """
    result = await run_scenario(QA_FAILS_TWICE, "REQ-030", "회원가입")

    assert result.state is RequirementState.ACCEPTED
    assert result.revision == 3

    dev = sorted(
        [t for t in result.tasks if t.agent == "dev"],
        key=lambda t: (t.revision, t.attempt),
    )
    dead = [t for t in dev if t.state == "failed"]
    assert len(dead) == 1, f"크래시한 dev 행이 정확히 하나여야 한다: {dev}"
    assert dead[0].revision == 2 and dead[0].attempt == 1

    # Task 행은 불변이다 — 죽은 행을 되살린 것이 아니라 새 행이 생겼다.
    revived = [t for t in dev if t.revision == 2 and t.state == "completed"]
    assert len(revived) == 1
    assert revived[0].attempt == 2
    assert revived[0].task_id != dead[0].task_id
    assert revived[0].idempotency_key != dead[0].idempotency_key

    # 크래시 사실이 아웃박스에 남는다.
    failures = [e for e in result.events if e.event_type == "task_failed"]
    assert len(failures) == 1
    assert failures[0].payload["agent"] == "dev"


# --------------------------------------------------------------- 케이스 2


async def test_remediating_with_bumped_revision_and_no_tasks_is_resumed() -> None:
    """`remediate`의 두 트랜잭션 사이에서 죽은 상태를 리컨실러가 이어받는다.

    revision은 2로 올라갔는데 revision 2의 Task가 0개이고 상태는 remediating이다.
    이 조합은 Task 11 구현자가 DB에서 실제로 관측한 것이고, 여기서 계약으로 고정한다.
    """
    rid = "REQ-031"
    await reset_agents(ALL_PASS)
    await purge(rid)
    try:
        async with db_session() as s:
            s.add(
                WorkflowRequirement(
                    requirement_id=rid,
                    title="회원가입",
                    state=RequirementState.REMEDIATING.value,
                    revision=2,
                    run_id=f"run-{rid}",
                    created_at=_old(),
                    updated_at=_old(),
                )
            )
            await s.flush()
            for agent, verdict in (
                ("planner", None), ("dev", None), ("qa", "FAIL"), ("security", "FAIL")
            ):
                s.add(
                    WorkflowTask(
                        requirement_id=rid,
                        agent=agent,
                        revision=1,
                        a2a_task_id=f"fake-{rid}-{agent}",
                        idempotency_key=idempotency_key(rid, agent, 1, []),
                        state="completed",
                        verdict=verdict,
                        created_at=_old(),
                        completed_at=_old(),
                    )
                )
            await s.commit()

        resumed = await wait_for(
            rid,
            lambda r: any(t.agent == "dev" and t.revision == 2 for t in r.tasks),
            timeout_s=RECOVERY_TIMEOUT_S,
            what="revision 2의 dev 디스패치",
        )
        # revision을 **다시** 올리지 않았다 — 중단된 환류를 이어받았을 뿐이다.
        assert resumed.revision == 2
        dev2 = next(t for t in resumed.tasks if t.agent == "dev" and t.revision == 2)
        dev1 = next(t for t in resumed.tasks if t.agent == "dev" and t.revision == 1)
        assert dev2.revision_of == dev1.task_id  # 계보는 그대로 이어진다

        final = await wait_for(
            rid,
            lambda r: r.state is RequirementState.ACCEPTED,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="accepted",
        )
        assert final.revision == 2
    finally:
        await purge(rid)


# --------------------------------------------------------------- 케이스 3


async def test_submitted_without_agent_task_id_is_superseded() -> None:
    """`submit()`이 터져 `a2a_task_id`가 NULL인 채 남은 행을 갈아 끼운다.

    에이전트에 물어볼 대상조차 없으므로(우리가 받은 id가 없다) 그 행은 실패로
    확정하고 같은 에이전트를 새 행으로 다시 디스패치한다.
    """
    rid = "REQ-032"
    await reset_agents(ALL_PASS)
    await purge(rid)
    try:
        async with db_session() as s:
            s.add(
                WorkflowRequirement(
                    requirement_id=rid,
                    title="회원가입",
                    state=RequirementState.PLANNED.value,
                    revision=1,
                    run_id=f"run-{rid}",
                    created_at=_old(),
                    updated_at=_old(),
                )
            )
            await s.flush()
            s.add(
                WorkflowTask(
                    requirement_id=rid,
                    agent="planner",
                    revision=1,
                    a2a_task_id=None,
                    idempotency_key=idempotency_key(rid, "planner", 1, []),
                    state="submitted",
                    created_at=_old(),
                )
            )
            await s.commit()

        recovered = await wait_for(
            rid,
            lambda r: len([t for t in r.tasks if t.agent == "planner"]) == 2,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="planner 재디스패치",
        )
        orphan = next(t for t in recovered.tasks if t.attempt == 1)
        fresh = next(t for t in recovered.tasks if t.attempt == 2)
        assert orphan.state == "failed"
        assert fresh.a2a_task_id is not None or fresh.state == "submitted"

        final = await wait_for(
            rid,
            lambda r: r.state is RequirementState.ACCEPTED,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="accepted",
        )
        assert final.revision == 1
    finally:
        await purge(rid)


# ----------------------------------------- 상태가 Task 진행보다 뒤처진 경우

async def _seed(rid: str, state: RequirementState, rows: list[dict]) -> None:
    """크래시가 남긴 DB 상태를 손으로 만든다(요구사항 1 + 완료된 Task n)."""
    async with db_session() as s:
        s.add(
            WorkflowRequirement(
                requirement_id=rid,
                title="회원가입",
                state=state.value,
                revision=1,
                run_id=f"run-{rid}",
                created_at=_old(),
                updated_at=_old(),
            )
        )
        await s.flush()
        for row in rows:
            agent = row["agent"]
            s.add(
                WorkflowTask(
                    requirement_id=rid,
                    agent=agent,
                    revision=1,
                    a2a_task_id=f"fake-{rid}-{agent}",
                    idempotency_key=idempotency_key(rid, agent, 1, []),
                    state="completed",
                    verdict=row.get("verdict"),
                    created_at=_old(),
                    completed_at=_old(),
                )
            )
        await s.commit()


async def test_lost_transition_after_dev_is_picked_up() -> None:
    """dev는 끝났는데 `verifying` 전이와 검증 디스패치 직전에 죽은 경우.

    Task 행만 보면 할 일이 없어 보이지만 상태가 뒤처져 있다. 리컨실러는 상태
    기계를 흉내 내지 않고 엔진의 `advance`를 그대로 다시 부른다 — 전이 규칙이
    두 곳에 생기면 반드시 갈라지기 때문이다.
    """
    rid = "REQ-033"
    await reset_agents(ALL_PASS)
    await purge(rid)
    try:
        await _seed(
            rid,
            RequirementState.IMPLEMENTING,
            [{"agent": "planner"}, {"agent": "dev"}],
        )
        final = await wait_for(
            rid,
            lambda r: r.state is RequirementState.ACCEPTED,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="accepted",
        )
        assert {t.agent for t in final.tasks} == {"planner", "dev", "qa", "security"}
        assert final.revision == 1
    finally:
        await purge(rid)


async def test_lost_judgement_with_both_verdicts_is_picked_up() -> None:
    """두 verdict가 다 모인 직후에 죽은 경우 — 판정만 유실됐다.

    에이전트를 다시 부를 필요가 없다. 이미 있는 근거로 판정만 내리면 된다.
    """
    rid = "REQ-034"
    await reset_agents(ALL_PASS)
    await purge(rid)
    try:
        await _seed(
            rid,
            RequirementState.VERIFYING,
            [
                {"agent": "planner"},
                {"agent": "dev"},
                {"agent": "qa", "verdict": "PASS"},
                {"agent": "security", "verdict": "PASS"},
            ],
        )
        final = await wait_for(
            rid,
            lambda r: r.state is RequirementState.ACCEPTED,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="accepted",
        )
        assert final.task_count == 4  # 새 Task를 만들지 않았다 — 판정만 했다.
    finally:
        await purge(rid)


# --------------------------------------------------------------- 케이스 4

#: SIGKILL 시점을 시계가 아니라 **DB에서 관측되는 진행 상태**에 고정한다.
#: `asyncio.sleep(1.5)` 같은 대기는 머신 부하에 따라 어디에 떨어질지 모른다.
KILL_POINTS: list[tuple[str, object]] = [
    ("planner_dispatched", lambda r: any(t.agent == "planner" for t in r.tasks)),
    ("implementing", lambda r: r.state is RequirementState.IMPLEMENTING),
    ("verifying", lambda r: r.state is RequirementState.VERIFYING),
    ("second_revision", lambda r: r.revision == 2),
]


@pytest.mark.parametrize(
    "point,predicate", KILL_POINTS, ids=[p[0] for p in KILL_POINTS]
)
async def test_orchestrator_sigkill_mid_run_still_completes(point, predicate) -> None:
    """임의 시점에 오케스트레이터를 SIGKILL해도 재기동만으로 끝까지 간다.

    에이전트 4종은 계속 살아 있다(`depends_on`은 런타임 연결이 아니다). 죽어 있는
    동안 도착한 푸시는 전부 유실되므로, 복구는 전적으로 리컨실러의 관찰에 달려 있다.

    `verdicts_fail_twice`를 쓴다 — `qa_fails_twice`가 아니다. 그쪽의
    `dev: attempt_2: crash`는 **스텁의 호출 순번**에 걸려 있어서, SIGKILL이
    유발한 재전송이 dev 를 한 번 더 부르면 그 2회차가 재시도 자리에 떨어진다.
    그러면 dev 는 transport(1회차 유실) → execution(2회차 크래시)로 연달아 실패하고
    `max_attempts(execution)=2`가 소진돼 정당하게 escalate 한다 — 복구 결함이 아니라
    **두 고장 주입이 같은 재시도 예산을 나눠 쓴 것**이다(실측). 이 테스트의 관심사는
    오케스트레이터 크래시 복구 하나뿐이므로 에이전트 크래시는 빼고, verdict 순서만
    같은 시나리오로 동일한 환류 2회를 만든다. 에이전트 크래시 재시도는
    `test_dev_crash_on_second_attempt_is_retried_and_recovers`가 따로 덮는다.
    """
    rid = f"REQ-04-{point}"
    # 관찰을 시작하기 전에 지난 실행의 행을 지운다. run_scenario도 시작하며 지우지만
    # 그 삭제와 이 테스트의 첫 폴링은 경합한다 — 지난 실행(이미 accepted)을 이번
    # 실행으로 착각하면 kill이 허공에 떨어진다(실측).
    await purge(rid)
    run = asyncio.create_task(run_scenario(VERDICTS_FAIL_TWICE, rid, "회원가입"))
    try:
        at_kill = await wait_for(
            rid, predicate, timeout_s=KILL_POINT_TIMEOUT_S, what=f"kill 지점 {point}"
        )
        # kill이 종료 뒤에 도착하면 이 테스트는 아무것도 시험하지 않는다.
        assert at_kill.state not in TERMINAL, f"{point}: 이미 끝난 뒤였다"
        await kill_orchestrator()
        await restart_orchestrator()
        result = await run
    finally:
        if not run.done():
            run.cancel()
    assert result.state is RequirementState.ACCEPTED


async def test_result_matches_uninterrupted_run() -> None:
    """죽였다 살린 실행의 최종 상태가 중단 없는 실행과 같다.

    `all_pass_slow`를 쓴다 — `qa_fails_twice`의 verdict 커서는 스텁의 **호출 순번**에
    달려 있어, 복구가 에이전트를 한 번 더 부르면 회차 수가 정당하게 달라진다
    (커서가 앞당겨져 더 일찍 PASS한다). 그 시나리오로 "동일성"을 재면 무엇을
    재는지 모르게 된다. all_pass 계열은 호출 순번과 무관하므로 최종 상태가
    킬 타이밍에 대해 불변이고, 그래서 비교가 의미를 갖는다. 지연을 넣은 변종을
    쓰는 이유는 kill이 실행 **도중에** 떨어지게 하기 위해서다(실측: 지연 없는
    all_pass는 kill 명령이 도착하기 전에 끝나버려 테스트가 공회전했다).

    비교 대상은 **결과**다: 상태·회차·완료된 (에이전트, 회차) 집합·아티팩트.
    Task 행 **개수**는 비교하지 않는다 — 복구는 죽은 행을 되살리지 않고 새 행을
    만들므로(전역 제약) 크래시를 겪은 실행은 행이 더 많은 것이 정상이다.
    """
    clean = await run_scenario(ALL_PASS_SLOW, "REQ-050", "회원가입")

    rid = "REQ-051"
    await purge(rid)  # 위와 같은 이유 — 관찰 전에 지운다.
    run = asyncio.create_task(run_scenario(ALL_PASS_SLOW, rid, "회원가입"))
    try:
        at_kill = await wait_for(
            rid,
            lambda r: any(t.agent == "planner" for t in r.tasks),
            timeout_s=KILL_POINT_TIMEOUT_S,
            what="planner 디스패치",
        )
        assert at_kill.state not in TERMINAL, "kill이 종료 뒤에 도착했다"
        await kill_orchestrator()
        await restart_orchestrator()
        killed = await run
    finally:
        if not run.done():
            run.cancel()

    assert killed.state == clean.state is RequirementState.ACCEPTED
    assert killed.revision == clean.revision == 1
    assert _completed(killed) == _completed(clean)
    assert _artifacts(killed) == _artifacts(clean)


def _completed(result: ScenarioResult) -> set[tuple[str, int]]:
    return {(t.agent, t.revision) for t in result.tasks if t.state == "completed"}


def _artifacts(result: ScenarioResult) -> set[tuple[str, int]]:
    return {(a.kind, a.version) for a in result.artifacts}


# ------------------------------------------------------- 평시에는 조용하다


async def test_reconciler_does_not_disturb_a_healthy_run() -> None:
    """리컨실러가 상시로 돌지만 정상 실행에는 흔적을 남기지 않는다.

    평시에도 도는 루프여야 크래시 복구가 특수 경로가 되지 않는다. 그 대가로
    "진행 중인 작업에 손대지 않는다"가 반드시 성립해야 한다 — 에이전트마다
    완료 행이 정확히 하나여야 하고 실패 행은 없어야 한다.
    """
    result = await run_scenario(ALL_PASS, "REQ-052", "로그인")
    assert result.state is RequirementState.ACCEPTED
    assert result.task_count == 4
    assert sorted(t.agent for t in result.tasks) == ["dev", "planner", "qa", "security"]
    assert all(t.state == "completed" and t.attempt == 1 for t in result.tasks)
