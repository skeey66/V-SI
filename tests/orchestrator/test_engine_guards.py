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

import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.engine import DuplicateRequirement, WorkflowEngine
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


async def test_starting_an_existing_requirement_raises_duplicate(session) -> None:
    """같은 `requirement_id`를 두 번 열면 전용 예외가 나온다.

    예전에는 유니크 제약 위반이 `IntegrityError` 그대로 `POST /requirements`를
    빠져나가 500이 됐다 — 데모의 정문에서 "요구사항을 두 번 넣었다"가
    "오케스트레이터가 고장났다"로 보였다.
    """
    rid = "REQ-G-dup"
    db, maker, workflow = await _engine_over(rid, RequirementState.PLANNED, session)
    try:
        with pytest.raises(DuplicateRequirement, match=rid):
            await workflow.start(rid, "회원가입", "run-dup")
        # 두 번째 시도가 아무 흔적도 남기지 않았다(이벤트도 Task도 없다).
        await _assert_untouched(maker, rid, RequirementState.PLANNED)
    finally:
        await db.dispose()


class _GatedEngine(WorkflowEngine):
    """`remediate`의 두 번째 트랜잭션(`_transition`) 직전에 한 번 멈출 수 있는 엔진.

    `remediate`는 트랜잭션이 둘이고(회차 증가 / 상태 전이) **첫 트랜잭션은
    상태를 바꾸지 않는다** — 그래서 뒤늦게 락을 얻은 두 번째 호출자도 여전히
    REMEDIATING을 보고 첫 가드를 통과한다. 그 창을 결정적으로 재현하려고
    `_transition` 진입 직전에 게이트를 건다(전이 로직 자체는 건드리지 않고
    `super()`로 그대로 넘긴다).

    `dispatch_agent`는 기록만 한다 — 에이전트 클라이언트 없이 "몇 번
    디스패치됐는가"만 보면 되기 때문이다.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.dispatched: list[str] = []
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.pause_next_transition = False

    async def dispatch_agent(self, requirement_id: str, agent: str) -> None:
        self.dispatched.append(agent)

    async def _transition(self, *args, **kwargs) -> bool:
        if self.pause_next_transition:
            self.pause_next_transition = False
            self.paused.set()
            await self.resume.wait()
        return await super()._transition(*args, **kwargs)


async def test_remediate_does_not_dispatch_when_it_loses_the_transition(session) -> None:
    """경쟁에서 진 `remediate`는 dev를 **디스패치하지 않는다**.

    원장의 옛 논거("진 호출자는 `IllegalTransition`으로 죽는다")는 거짓이다 —
    `(IMPLEMENTING, DEV_DONE) → VERIFYING`은 전이 표에 있는 **합법** 전이라
    진 호출자는 죽지 않고 한 칸 더 간다. 그러면 revision R+1에 dev 행 2개,
    검증자 행 0개, 상태 `verifying`이 남는다: 아무도 그 두 번째 dev를
    기다리지 않고 verdict도 영영 모이지 않는다.

    그래서 `expected=REMEDIATING`이 필요하다 — 전이가 내 것이 아니었으면
    디스패치도 내 것이 아니다.
    """
    rid = "REQ-G-remediate-race"
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회원가입",
            state=RequirementState.REMEDIATING.value, revision=1, run_id=f"run-{rid}",
        )
    )
    session.add(
        WorkflowTask(
            requirement_id=rid, agent="dev", revision=1,
            idempotency_key="k-remediate-race", state="completed", attempt=1,
        )
    )
    await session.commit()

    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = _GatedEngine(maker, {}, TimeoutConfig.from_env({}))
    try:
        # 호출자 A: 회차를 1 → 2로 올린 직후 전이 직전에 멈춘다.
        workflow.pause_next_transition = True
        loser = asyncio.create_task(workflow.remediate(rid))
        await asyncio.wait_for(workflow.paused.wait(), timeout=10)

        # 호출자 B: 같은 창에서 전부 통과한다. 상태는 아직 REMEDIATING이고
        # revision 2에는 Task가 없으므로 회차를 또 올리지도 않는다.
        await workflow.remediate(rid)

        workflow.resume.set()
        await asyncio.wait_for(loser, timeout=10)

        assert workflow.dispatched == ["dev"], (
            f"dev 디스패치는 정확히 한 번이어야 한다: {workflow.dispatched}"
        )
        async with maker() as s:
            req = await s.get(WorkflowRequirement, rid)
            assert RequirementState(req.state) is RequirementState.IMPLEMENTING
            assert req.revision == 2
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
