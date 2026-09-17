"""엔진의 재개 가능한 공개 진입점이 **자기 전제를 스스로 검사하는지** 고정한다.

리컨실러(Task 13)는 요구사항 행의 잠금을 놓은 뒤에 엔진을 부른다 — 그 사이에
푸시가 도착해 상태가 먼저 움직일 수 있다. 그때 엔진이 맹목적으로 전이하면
`IllegalTransition`이 터진다. 시스템은 다음 주기에 스스로 회복하지만, **공개
계약이 거짓이면 다음 호출자(Task 12)에게는 함정**이다. 그래서 "전제가 어긋나면
아무 것도 하지 않고 돌아간다"를 계약으로 못 박는다.

에이전트 클라이언트를 빈 dict로 넘긴다: 가드가 제대로 동작하면 디스패치까지
가지 않으므로 클라이언트가 필요 없다. 가드가 없으면 그 전에 전이에서 터진다.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.engine import WorkflowEngine
from orchestrator.models import OutboxEvent, WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.retry import FailureClass
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


async def _engine_over(requirement_id: str, state: RequirementState, session):
    session.add(
        WorkflowRequirement(
            requirement_id=requirement_id,
            title="회원가입",
            state=state.value,
            revision=1,
            run_id=f"run-{requirement_id}",
        )
    )
    await session.commit()
    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    return db, maker, WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))


async def _assert_untouched(maker, requirement_id: str, state: RequirementState):
    async with maker() as s:
        req = await s.get(WorkflowRequirement, requirement_id)
        assert RequirementState(req.state) is state
        tasks = (
            await s.execute(
                select(WorkflowTask).where(
                    WorkflowTask.requirement_id == requirement_id
                )
            )
        ).scalars().all()
        assert list(tasks) == []


@pytest.mark.parametrize(
    "finished_agent,moved_on",
    [
        # planner가 끝났다고 알려 왔지만 상태는 이미 implementing이다
        # (다른 경로가 먼저 전이시켰다).
        ("planner", RequirementState.IMPLEMENTING),
        # dev가 끝났다고 알려 왔지만 상태는 이미 verifying이다.
        ("dev", RequirementState.VERIFYING),
    ],
)
async def test_advance_does_nothing_when_state_already_moved_on(
    session, finished_agent, moved_on
) -> None:
    rid = f"REQ-G-{finished_agent}"
    db, maker, workflow = await _engine_over(rid, moved_on, session)
    try:
        await workflow.advance(rid, finished_agent)  # 터지지 않는다
        await _assert_untouched(maker, rid, moved_on)
    finally:
        await db.dispose()


async def test_advance_transitions_when_premise_holds(session) -> None:
    """가드가 정상 경로를 막지 않는다 — 전제가 맞으면 전이는 그대로 일어난다."""
    rid = "REQ-G-ok"
    db, maker, workflow = await _engine_over(rid, RequirementState.PLANNED, session)
    try:
        # planner 분기는 전이 뒤에 dev를 디스패치한다. 클라이언트가 없으므로
        # 디스패치에서 KeyError가 나지만, 그 전에 전이는 커밋돼 있어야 한다.
        with pytest.raises(KeyError):
            await workflow.advance(rid, "planner")
        async with maker() as s:
            req = await s.get(WorkflowRequirement, rid)
            assert RequirementState(req.state) is RequirementState.IMPLEMENTING
    finally:
        await db.dispose()


@pytest.mark.parametrize(
    "state",
    [RequirementState.PLANNED, RequirementState.IMPLEMENTING, RequirementState.VERIFYING],
)
async def test_give_up_transitions_when_premise_holds(session, state) -> None:
    """Task 12: 리컨실러가 캡을 넘겼다고 판단한 상태 그대로면 ESCALATED로 간다."""
    rid = f"REQ-G-giveup-{state.value}"
    db, maker, workflow = await _engine_over(rid, state, session)
    try:
        await workflow.give_up(rid, "dev", FailureClass.EXECUTION)
        async with maker() as s:
            req = await s.get(WorkflowRequirement, rid)
            assert RequirementState(req.state) is RequirementState.ESCALATED
    finally:
        await db.dispose()


async def test_give_up_records_agent_and_failure_class_in_the_event(session) -> None:
    """리뷰 라운드 1: 운영자가 "누가·왜 포기됐는지"를 아웃박스에서 읽을 수 있어야 한다.

    `to == "escalated"`만으로는 회차 상한 초과(`remediate`)와 재시도 예산 소진
    (`give_up`)을 구분할 수 없다 — `reason`·`agent`·`failure_class`를 같은
    `state_changed` 이벤트 페이로드에 남긴다.
    """
    rid = "REQ-G-giveup-event"
    db, maker, workflow = await _engine_over(rid, RequirementState.IMPLEMENTING, session)
    try:
        await workflow.give_up(rid, "dev", FailureClass.EXECUTION)
        async with maker() as s:
            events = (
                await s.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == rid,
                        OutboxEvent.event_type == "state_changed",
                    )
                )
            ).scalars().all()
        escalations = [e for e in events if e.payload.get("to") == "escalated"]
        assert len(escalations) == 1
        payload = escalations[0].payload
        assert payload["reason"] == "retry_budget_exhausted"
        assert payload["agent"] == "dev"
        assert payload["failure_class"] == "execution"
    finally:
        await db.dispose()


async def test_give_up_does_nothing_when_state_already_moved_on(session) -> None:
    """전이 사이에 다른 경로가 먼저 끝냈으면(예: ACCEPTED) 아무 것도 하지 않는다."""
    rid = "REQ-G-giveup-moved"
    db, maker, workflow = await _engine_over(rid, RequirementState.ACCEPTED, session)
    try:
        await workflow.give_up(rid, "dev", FailureClass.EXECUTION)  # 터지지 않는다
        await _assert_untouched(maker, rid, RequirementState.ACCEPTED)
    finally:
        await db.dispose()


async def test_give_up_does_not_touch_remediating(session) -> None:
    """REMEDIATING의 반복 상한은 `remediate` 소관이다 — `give_up`이 가로채지 않는다.

    두 메서드가 같은 신호(`LIMIT_EXCEEDED`)로 같은 목적지(ESCALATED)에 가지만,
    "누가 그 판단을 내리는가"는 상태별로 하나로 고정돼야 한다 — 안 그러면
    회차 증가 로직(`remediate`의 두 트랜잭션) 없이 REMEDIATING을 건너뛸 수 있다.
    """
    rid = "REQ-G-giveup-remediating"
    db, maker, workflow = await _engine_over(rid, RequirementState.REMEDIATING, session)
    try:
        await workflow.give_up(rid, "dev", FailureClass.EXECUTION)
        await _assert_untouched(maker, rid, RequirementState.REMEDIATING)
    finally:
        await db.dispose()
