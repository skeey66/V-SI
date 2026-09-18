import pytest
from sqlalchemy import select
from orchestrator.models import WorkflowRequirement, OutboxEvent
from orchestrator.outbox import record_event
from orchestrator.workflow import RequirementState


async def test_state_change_and_event_commit_together(session):
    req = WorkflowRequirement(requirement_id="REQ-001", title="회원가입",
                              state=RequirementState.PLANNED, run_id="run-1")
    session.add(req)
    await session.commit()

    req.state = RequirementState.IMPLEMENTING
    record_event(session, "requirement", "REQ-001", "state_changed",
                 {"to": "implementing"})
    await session.commit()

    events = (await session.execute(select(OutboxEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].published_at is None
    assert events[0].payload["to"] == "implementing"


async def test_rollback_discards_both(session):
    req = WorkflowRequirement(requirement_id="REQ-002", title="로그인",
                              state=RequirementState.PLANNED, run_id="run-1")
    session.add(req)
    await session.commit()

    req.state = RequirementState.IMPLEMENTING
    record_event(session, "requirement", "REQ-002", "state_changed", {"to": "implementing"})
    await session.rollback()

    events = (await session.execute(select(OutboxEvent))).scalars().all()
    fresh = await session.get(WorkflowRequirement, "REQ-002")
    assert events == []
    assert fresh.state == RequirementState.PLANNED
