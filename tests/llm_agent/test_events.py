import pytest
from sqlalchemy import select

from llm_agent.events import record_tool_event
from orchestrator.models import OutboxEvent


async def test_writes_advisory_event_with_requirement_aggregate(session_maker) -> None:
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="dev", revision=2,
        tool="write_file", result={"ok": True, "detail": "썼다"},
    )
    async with session_maker() as s:
        rows = (
            await s.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == "REQ-1")
                .order_by(OutboxEvent.event_id)
            )
        ).scalars().all()
    assert [r.event_type for r in rows] == ["tool_result"]
    assert rows[0].aggregate == "requirement"
    assert rows[0].aggregate_id == "REQ-1"
    assert rows[0].payload["agent"] == "dev"
    assert rows[0].payload["tool"] == "write_file"
    assert rows[0].payload["revision"] == 2
    assert rows[0].payload["ok"] is True


async def test_exit_code_is_carried_when_present(session_maker) -> None:
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="qa", revision=1,
        tool="run_tests", result={"ok": False, "detail": "1 failed", "exit_code": 1},
    )
    async with session_maker() as s:
        row = (
            await s.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == "REQ-1"))
        ).scalars().one()
    assert row.payload["exit_code"] == 1


async def test_event_carries_no_state_field(session_maker) -> None:
    """자문 이벤트다 — 상태를 유도할 수 있는 필드를 실으면 안 된다 (스펙 §8.1)."""
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="dev", revision=1,
        tool="write_file", result={"ok": True, "detail": "x"},
    )
    async with session_maker() as s:
        row = (
            await s.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == "REQ-1"))
        ).scalars().one()
    assert "state" not in row.payload
    assert "verdict" not in row.payload


async def test_detail_is_truncated_to_keep_events_small(session_maker) -> None:
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="qa", revision=1,
        tool="run_tests", result={"ok": False, "detail": "x" * 5000, "exit_code": 1},
    )
    async with session_maker() as s:
        row = (
            await s.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == "REQ-1"))
        ).scalars().one()
    assert len(row.payload["detail"]) <= 512


async def test_result_supplied_state_or_verdict_keys_do_not_leak_into_payload(session_maker) -> None:
    """회귀 방지: `payload` 는 필드별로 명시적으로 골라 만든다 — `{**result, ...}`
    처럼 통째로 펼치는 코드로 퇴행하면, 도구 결과 자체에 `state`/`verdict` 키가
    실려 있을 때 그대로 새어나간다. 현재 구현은 화이트리스트라 안전하지만, 그
    사실을 지키는 테스트가 없었다."""
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="dev", revision=1,
        tool="write_file",
        result={"ok": True, "detail": "x", "state": "DONE", "verdict": "PASS"},
    )
    async with session_maker() as s:
        row = (
            await s.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == "REQ-1"))
        ).scalars().one()
    assert "state" not in row.payload
    assert "verdict" not in row.payload
