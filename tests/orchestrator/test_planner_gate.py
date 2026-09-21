"""기획 게이트: 인수 테스트가 아무것도 검사하지 않으면 개발을 보내지 않는다.

**이 게이트는 추측이 아니라 실측에서 나왔다.** 실제 LLM 실행 두 건:

  REQ-CART-095427  기획이 모듈 최상단 `assert` 만 씀 → pytest 수집 0개(exit 5)
                   → QA 가 exit 5 로 반려 → 환류 1회를 헛돌고 통과
  REQ-SITE-101435  기획이 본문을 `assert True` 로 비워둠 → 구현 없이도 4개 통과
                   → 개발이 목표 없이 3회차까지 헛돌다 1시간 18분 만에 escalated

둘 다 **기획이 끝난 직후에 알 수 있었다** — 구현이 하나도 없는 워크스페이스에서
그 테스트를 돌려보면 된다. 쓸 만한 인수 테스트는 그 상태에서 반드시 실패한다
(부를 함수가 없으므로). 통과한다면 아무것도 검사하지 않는다는 뜻이다.

판정은 여기서도 모델이 아니라 `check_acceptance_tests` 의 종료 코드가 한다.
엔진은 그 결과가 찍힌 `WorkflowTask.verdict` 만 본다.

게이트에 걸리면 `escalated`(종료)가 아니라 `blocked`(승인 대기)로 간다. 요구사항
자체는 멀쩡할 수 있고, 고쳐야 할 것은 확인 항목뿐이기 때문이다 — 사람이 보고
승인하면 그 자리에서 이어진다.
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.a2a_client import TaskSnapshot
from orchestrator.db import make_engine
from orchestrator.engine import WorkflowEngine
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


class _CountingAgentClient:
    """디스패치가 **일어났는지**만 센다. 게이트의 요점이 그것이다."""

    def __init__(self) -> None:
        self.submits: list[dict] = []

    async def submit(self, payload: dict, key: str) -> str:
        self.submits.append(payload)
        return f"a2a-dev-{len(self.submits)}"

    async def get_task(self, a2a_task_id: str) -> TaskSnapshot:
        return TaskSnapshot(a2a_task_id=a2a_task_id, state="TASK_STATE_WORKING")


async def _planned(session, rid: str):
    """PLANNED 상태의 요구사항 하나와 열린 planner 행을 만든다."""
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회의실 예약", state=RequirementState.PLANNED.value,
            revision=1, run_id=f"run-{rid}",
        )
    )
    session.add(
        WorkflowTask(
            requirement_id=rid, agent="planner", revision=1,
            a2a_task_id=f"a2a-{rid}-planner", idempotency_key=f"k-{rid}-planner",
            state="working", attempt=1,
        )
    )
    await session.commit()
    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    dev = _CountingAgentClient()
    return db, maker, dev, WorkflowEngine(maker, {"dev": dev}, TimeoutConfig.from_env({}))


async def _state(maker, rid: str) -> str:
    async with maker() as s:
        return (
            await s.execute(
                select(WorkflowRequirement.state).where(
                    WorkflowRequirement.requirement_id == rid
                )
            )
        ).scalar_one()


async def test_vacuous_acceptance_tests_block_instead_of_dispatching_dev(session) -> None:
    """REQ-SITE-101435 의 재현: 구현 없이 테스트가 통과해버린 경우.

    `check_acceptance_tests` 가 exit 1 을 내고, 엔진은 개발을 보내지 않는다.
    """
    rid = "REQ-GATE-vacuous"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        await workflow.on_task_completed(
            f"a2a-{rid}-planner",
            {"kind": "requirements", "agent": "planner", "exit_code": 1,
             "summary": "구현이 없는데 테스트가 전부 통과했다"},
        )
        assert await _state(maker, rid) == RequirementState.BLOCKED.value
        assert dev.submits == []  # 목표가 비었는데 개발을 보내면 안 된다.
    finally:
        await db.dispose()


async def test_uncollectable_acceptance_tests_also_block(session) -> None:
    """REQ-CART-095427 의 재현: 수집 0개(pytest exit 5).

    도구가 exit 5 를 1 로 옮겨 오므로 엔진이 보는 모양은 위와 같다 — 게이트는
    "왜 쓸모없는가"를 구분하지 않는다. 구분은 사람에게 보여줄 `summary` 의 몫이다.
    """
    rid = "REQ-GATE-nocollect"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        await workflow.on_task_completed(
            f"a2a-{rid}-planner",
            {"kind": "requirements", "agent": "planner", "exit_code": 1,
             "summary": "확인 항목이 하나도 수집되지 않았다"},
        )
        assert await _state(maker, rid) == RequirementState.BLOCKED.value
        assert dev.submits == []
    finally:
        await db.dispose()


async def test_meaningful_acceptance_tests_dispatch_dev_as_before(session) -> None:
    """게이트는 정상 경로를 건드리지 않는다 — 통과하면 예전 그대로 개발로 간다."""
    rid = "REQ-GATE-ok"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        await workflow.on_task_completed(
            f"a2a-{rid}-planner",
            {"kind": "requirements", "agent": "planner", "exit_code": 0,
             "summary": "구현이 없어 예상대로 실패했다"},
        )
        assert await _state(maker, rid) == RequirementState.IMPLEMENTING.value
        assert len(dev.submits) == 1
    finally:
        await db.dispose()


async def test_stub_mode_planner_never_blocks(session) -> None:
    """스텁 모드는 게이트에 걸리지 않는다 — SP1 데모와 통합 시험이 그대로 돈다.

    `stub_agent.executor` 는 verdict 가 없는 역할(planner/dev)에 `exit_code = 0`
    을 찍는다. 그래서 게이트는 스텁을 PASS 로 본다. 이 시험이 지키는 것은
    "게이트를 켠 대가로 기존 경로가 멈추지 않는다"이다.
    """
    rid = "REQ-GATE-stub"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        # `stub_agent.executor.build_payload` 가 planner 에게 만드는 모양 그대로.
        await workflow.on_task_completed(
            f"a2a-{rid}-planner",
            {"kind": "requirements", "agent": "planner", "exit_code": 0},
        )
        assert await _state(maker, rid) == RequirementState.IMPLEMENTING.value
        assert len(dev.submits) == 1
    finally:
        await db.dispose()


async def test_planner_row_without_a_verdict_is_treated_as_pass(session) -> None:
    """게이트 도입 **전에** 만들어진 행(verdict NULL)은 막지 않는다.

    이미 돌고 있던 요구사항이 배포 한 번으로 승인 대기에 갇히면 안 된다.
    모호하면 막지 않는 쪽을 고른다 — 게이트의 목적은 헛도는 환류를 줄이는
    것이지 새로운 정지 사유를 만드는 것이 아니다.

    `on_task_completed` 을 타지 않고 `advance` 를 직접 부르는 이유: 완료 경로는
    언제나 verdict 를 찍으므로, NULL 인 행은 그 경로 **밖에서** 만들어진 것만
    존재한다. 그 상황을 그대로 재현한다.
    """
    rid = "REQ-GATE-legacy"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        async with maker() as s:
            row = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.a2a_task_id == f"a2a-{rid}-planner"
                    )
                )
            ).scalar_one()
            row.state = "completed"
            row.verdict = None
            await s.commit()

        await workflow.advance(rid, "planner")
        assert await _state(maker, rid) == RequirementState.IMPLEMENTING.value
        assert len(dev.submits) == 1
    finally:
        await db.dispose()


async def test_approve_releases_the_gate_and_dispatches_dev(session) -> None:
    """사람이 승인하면 그 자리에서 개발이 나간다 — 처음부터 다시 돌지 않는다."""
    rid = "REQ-GATE-approve"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        await workflow.on_task_completed(
            f"a2a-{rid}-planner",
            {"kind": "requirements", "agent": "planner", "exit_code": 1, "summary": "비었다"},
        )
        assert await _state(maker, rid) == RequirementState.BLOCKED.value

        assert await workflow.approve(rid) is True
        assert await _state(maker, rid) == RequirementState.IMPLEMENTING.value
        assert len(dev.submits) == 1
    finally:
        await db.dispose()


async def test_approve_is_idempotent_and_never_double_dispatches(session) -> None:
    """두 번 눌러도 개발이 두 번 나가지 않는다.

    승인 버튼은 사람이 누른다 — 더블클릭·새로고침·재전송이 일상이다. 두 번째
    호출은 전제(`expected=BLOCKED`)가 깨져 조용히 물러난다.
    """
    rid = "REQ-GATE-twice"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        await workflow.on_task_completed(
            f"a2a-{rid}-planner",
            {"kind": "requirements", "agent": "planner", "exit_code": 1, "summary": "비었다"},
        )
        assert await workflow.approve(rid) is True
        assert await workflow.approve(rid) is False
        assert len(dev.submits) == 1
    finally:
        await db.dispose()


async def test_approve_on_a_requirement_that_is_not_blocked_does_nothing(session) -> None:
    """정상 진행 중인 요구사항에 승인을 걸어도 흐름을 흔들지 않는다."""
    rid = "REQ-GATE-notblocked"
    db, maker, dev, workflow = await _planned(session, rid)
    try:
        assert await workflow.approve(rid) is False
        assert await _state(maker, rid) == RequirementState.PLANNED.value
        assert dev.submits == []
    finally:
        await db.dispose()


async def test_approve_on_an_unknown_requirement_raises_lookup_error(session) -> None:
    """없는 ID 로 승인을 부르면 `LookupError` 다 — API 경계가 404 로 옮긴다.

    `_transition` 의 `scalar_one()` 은 행이 없으면 `NoResultFound` 를 던지는데
    그것은 `LookupError` 가 아니다. 그대로 두면 오타 난 ID 하나에 500 이 난다
    (통합 시험에서 실측으로 잡았다).
    """
    import pytest

    from orchestrator.db import make_engine

    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))
    try:
        with pytest.raises(LookupError):
            await workflow.approve("REQ-DOES-NOT-EXIST")
    finally:
        await db.dispose()
