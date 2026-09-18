from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from orchestrator.models import OutboxEvent


def record_event(session: AsyncSession, aggregate: str, aggregate_id: str,
                 event_type: str, payload: dict) -> OutboxEvent:
    """이벤트를 세션에 추가만 한다. commit은 호출자가 상태 전이와 함께 수행한다.

    이 함수가 commit하지 않는 것이 핵심이다. 상태 전이와 이벤트가
    같은 트랜잭션에 들어가야 "상태는 바뀌었는데 이벤트는 유실" 창이 닫힌다.
    """
    event = OutboxEvent(aggregate=aggregate, aggregate_id=aggregate_id,
                        event_type=event_type, payload=payload)
    session.add(event)
    return event
