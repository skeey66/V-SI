"""Task 12 통합 시험 — 실패 분류·재시도·재시도 상한(캡).

앞의 두 시험은 브리프 스케치 그대로다. **둘 다 Task 12의 새 코드 없이도 이미
통과한다** — Task 13(리컨실러)이 먼저 구현되면서 `dispatch_agent`가 `attempt`를
`COUNT+1`로 자동 계산하게 됐고, 리컨실러가 크래시한 dev 행을 관측해 새 행으로
재디스패치하기 때문이다(전송/실행/독성입력의 5분류·`classify`·`backoff_seconds`가
없어도 "죽은 행 대신 새 행을 보낸다"는 이미 성립한다). 그래서 이 두 시험은
Task 12 코드의 RED/GREEN 증거가 아니라 **회귀 방지**로 남긴다.

세 번째 시험이 Task 12가 실제로 추가하는 것 — **재시도 상한(캡)**을 시험한다.
`test_reconcile.py`의 케이스 2·3과 같은 방식으로 DB에 실패 상태를 손으로
만들어 놓고, **오케스트레이터 컨테이너 안에서 도는 진짜 리컨실러**가 세 번째
dev를 다시 보내는 대신 포기하는지 본다.

실측 메모(조사 과정에서 발견): dev 스텁을 **같은 컨테이너에서 연속으로 두 번**
크래시시켜 살아 있는 재시도로 재현하려 했으나, 두 번째 크래시에서 a2a-sdk
1.1.2의 이벤트 소비자(Consumer)가 실패 상태로의 전이를 누락하는 현상을
관측했다(Producer 쪽 로그는 남지만 Consumer 쪽 "Failed" 로그와 상태 전이가
없다 — `docker compose logs dev`로 재현 가능). 이는 스텁/SDK 쪽 문제이지
오케스트레이터의 캡 로직과 무관하므로, 여기서는 그 경로에 기대지 않고 DB에
직접 실패 행을 심어 캡만 독립적으로 시험한다. Task 14 이후 실제 크래시가
연속으로 나는 운영 시나리오를 다시 만난다면 이 관찰을 참고할 것.

네 번째 시험은 그 실측을 코드로 만든 안전망(`FORCE_FAIL`, 리뷰 라운드 1)을
시험한다: 위 SDK 결함이 실제로 발생해 어떤 행이 `working`에서 영원히 멈춰도
(get_task가 영영 비종료 상태만 돌려줘도) 시스템은 그 결함의 존재 여부와
무관하게 종료 상태에 도달해야 한다는 게 이 프로젝트의 핵심 보장이다. 진짜
연속 크래시는 비결정적이라(위 실측) 그 대신 "이미 영원히 열려 있는 행"이라는
관측 자체를 DB에 직접 심어 안전망만 독립적으로 시험한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orchestrator.idempotency import idempotency_key
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.reconciler import DEFAULT_STUCK_AFTER_S
from orchestrator.workflow import RequirementState
from tests.integration.harness import db_session, purge, reset_agents, run_scenario, wait_for

QA_FAILS_TWICE = "scenarios/qa_fails_twice.yaml"
ALL_PASS = "scenarios/all_pass.yaml"

RECOVERY_TIMEOUT_S = 90.0


def _old(seconds: float = 600.0) -> datetime:
    """리컨실러의 staleness 기준을 확실히 넘긴 과거 시각."""
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


async def test_dev_crash_on_second_attempt_is_retried_and_recovers():
    result = await run_scenario(QA_FAILS_TWICE, "REQ-020", "회원가입")
    dev = [t for t in result.tasks if t.agent == "dev"]
    assert any(t.attempt > 1 for t in dev), "재시도가 발생하지 않았다"
    assert result.state is RequirementState.ACCEPTED


async def test_no_duplicate_idempotency_keys():
    result = await run_scenario(QA_FAILS_TWICE, "REQ-021", "회원가입")
    keys = [t.idempotency_key for t in result.tasks]
    assert len(keys) == len(set(keys)), "멱등성 키가 중복됐다"


async def test_permanently_crashing_agent_stops_instead_of_looping_forever():
    """Task 12의 캡: EXECUTION 상한(2)을 채운 dev를 리컨실러가 더 재디스패치하지 않는다.

    `test_reconcile.py` 케이스 2·3과 같은 방식으로 크래시가 남긴 DB 상태를
    손으로 만든다 — planner는 끝났고 dev는 이미 두 번(캡만큼) 실패했다. 캡이
    없으면 리컨실러는 세 번째 dev를 계속 디스패치하며 `implementing`에 영원히
    머문다. 캡이 있으면 `give_up`이 상태를 `escalated`로 보내고 더 이상 새
    dev 행을 만들지 않는다.
    """
    rid = "REQ-022"
    await reset_agents(ALL_PASS)
    await purge(rid)
    try:
        async with db_session() as s:
            s.add(
                WorkflowRequirement(
                    requirement_id=rid,
                    title="회원가입",
                    state=RequirementState.IMPLEMENTING.value,
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
                    a2a_task_id=f"fake-{rid}-planner",
                    idempotency_key=idempotency_key(rid, "planner", 1, []),
                    state="completed",
                    created_at=_old(),
                    completed_at=_old(),
                )
            )
            for attempt in (1, 2):
                s.add(
                    WorkflowTask(
                        requirement_id=rid,
                        agent="dev",
                        revision=1,
                        attempt=attempt,
                        a2a_task_id=f"fake-{rid}-dev-{attempt}",
                        idempotency_key=idempotency_key(rid, "dev", 1, [], attempt),
                        state="failed",
                        failure_class="execution",
                        created_at=_old(),
                        completed_at=_old(),
                    )
                )
            await s.commit()

        final = await wait_for(
            rid,
            lambda r: r.state is RequirementState.ESCALATED,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="escalated (재시도 캡 소진)",
        )
        dev = [t for t in final.tasks if t.agent == "dev"]
        assert len(dev) == 2, f"캡을 넘겨 세 번째 dev 행이 생기면 안 된다: {dev}"
        assert all(t.state == "failed" for t in dev)  # 불변 — 기존 행을 되돌리지 않았다

        escalations = [e for e in final.events if e.payload.get("to") == "escalated"]
        assert len(escalations) == 1
        # 리뷰 라운드 1: 운영자가 회차 상한 초과(remediate)와 재시도 예산
        # 소진(give_up)을 구분하고, 어느 에이전트가 원인인지 알 수 있어야 한다.
        assert escalations[0].payload["reason"] == "retry_budget_exhausted"
        assert escalations[0].payload["agent"] == "dev"
        assert escalations[0].payload["failure_class"] == "execution"
    finally:
        await purge(rid)


async def test_stuck_row_past_ceiling_reaches_a_terminal_state_anyway():
    """Task 12 리뷰 라운드 1의 핵심 보장: SDK가 종료 상태를 영영 안 줘도 시스템은 멈추지 않는다.

    dev의 `working` 행 하나를 `stuck_after_s`보다 훨씬 오래된 것으로 심는다 —
    이 행에 대응하는 실제 a2a Task는 없다(`fake-...`). `FORCE_FAIL`이 `get_task`를
    부르지 않고 곧장 실패로 확정하므로 존재하지 않는 a2a Task를 참조해도
    문제가 없다는 것 자체가 "이 층에서 SDK를 신뢰하지 않는다"는 설계를 증명한다.
    강제 실패 뒤에는 평범한 실패 행(1회, EXECUTION 상한 미달)이라 리컨실러가
    새 dev를 정상적으로 재디스패치하고, `all_pass` 시나리오라 그다음은 정상
    완료된다.
    """
    rid = "REQ-023"
    await reset_agents(ALL_PASS)
    await purge(rid)
    try:
        async with db_session() as s:
            s.add(
                WorkflowRequirement(
                    requirement_id=rid,
                    title="회원가입",
                    state=RequirementState.IMPLEMENTING.value,
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
                    a2a_task_id=f"fake-{rid}-planner",
                    idempotency_key=idempotency_key(rid, "planner", 1, []),
                    state="completed",
                    created_at=_old(),
                    completed_at=_old(),
                )
            )
            s.add(
                WorkflowTask(
                    requirement_id=rid,
                    agent="dev",
                    revision=1,
                    attempt=1,
                    a2a_task_id=f"fake-{rid}-dev-stuck",
                    idempotency_key=idempotency_key(rid, "dev", 1, []),
                    state="working",  # 영원히 이 상태로 남는 유령 행 — 실제 a2a Task가 없다
                    created_at=_old(seconds=DEFAULT_STUCK_AFTER_S + 120),
                )
            )
            await s.commit()

        final = await wait_for(
            rid,
            lambda r: r.state is RequirementState.ACCEPTED,
            timeout_s=RECOVERY_TIMEOUT_S,
            what="stuck 행을 넘어 accepted",
        )
        dev = sorted((t for t in final.tasks if t.agent == "dev"), key=lambda t: t.attempt)
        stuck_row = dev[0]
        assert stuck_row.state == "failed"
        assert stuck_row.failure_class == "execution"
        revived = dev[1]
        assert revived.state == "completed"
        assert revived.task_id != stuck_row.task_id  # 불변 — 되살린 게 아니라 새 행
    finally:
        await purge(rid)
