"""이벤트 게이트웨이 — 전달 실패 창(read-but-not-delivered) 단위 시험.

`tests/integration/test_gateway.py`의 재기동 시험은 "게이트웨이가 죽어 있는
동안 기록된 이벤트가 살아남는가"를 증명한다. 이 파일은 그보다 좁고 날카로운
창을 겨눈다: **게이트웨이가 살아서 행을 읽었지만, 아직 아무에게도 전달하지
못한 순간**. 리뷰 라운드 1 전 구현은 읽자마자 `published_at`을 찍고
커밋한 뒤에야 소켓으로 보냈다 — 그 커밋과 전송 사이에 전송이 전부 실패하면
행은 영원히 "발행됨"으로 남은 채 아무에게도 전달되지 못한다. 이 파일은
`services/event_gateway/pump.py`의 순수 로직(`pump_once`)을 직접 불러
전송을 결정적으로 실패시켜, 그 창에서 유실이 나지 않는지 확인한다.

`services.event_gateway.main`(FastAPI 앱)이 아니라 `pump`를 import하는 게
중요하다 — `main`은 import 시점에 `VSI_DATABASE_URL`을 요구하고 OTel 전역
TracerProvider를 고정하는데(`setup_tracing`), 그 부작용이 같은 pytest
프로세스에서 나중에 도는 `tests/agent_runtime/test_telemetry.py`를 조용히
깨뜨린다(리뷰 라운드 1에서 실측). `pump.py`는 그런 부작용이 없다.

컨테이너를 거치지 않는 순수 단위 시험이라 `tests/orchestrator/*`와 같은
패턴(로컬 스키마를 매 시험마다 drop_all/create_all)을 쓴다 — 살아 있는
컴포즈 스택의 게이트웨이 컨테이너는 클라이언트가 하나도 안 붙어 있는 한
이 테이블을 건드리지 않으므로(빈 클라이언트 집합일 때 사이클을 건너뛰는
설계) 안전하다.
"""

from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.models import Base, OutboxEvent
from services.event_gateway.pump import pump_once

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


class FakeWebSocket:
    """전송 성공/실패를 결정적으로 제어할 수 있는 가짜 클라이언트."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.received: list[dict] = []

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("전송 실패 시뮬레이션")
        self.received.append(data)


@pytest_asyncio.fixture
async def maker():
    """`tests/conftest.py`의 `session` 픽스처와 같은 원리, session_factory를 노출한다."""
    engine = make_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield m
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()


async def test_undelivered_event_is_not_marked_and_is_retried_next_cycle(maker) -> None:
    async with maker() as s:
        s.add(
            OutboxEvent(
                aggregate="task",
                aggregate_id="t-window",
                event_type="task_submitted",
                payload={"agent": "dev"},
            )
        )
        await s.commit()

    # 사이클 1: 유일하게 연결된 클라이언트의 전송이 실패한다.
    failing_client = FakeWebSocket(fail=True)
    clients = {failing_client}

    last_id = await pump_once(maker, clients, 0)

    # 전달이 실패했으니 커서는 전진하면 안 되고, published_at도 NULL로 남아야 한다.
    assert last_id == 0
    async with maker() as s:
        rows = (await s.execute(select(OutboxEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].published_at is None
    # 실패한 소켓은 다음에 재시도되지 않도록 제거된다.
    assert failing_client not in clients

    # 사이클 2: 정상 클라이언트가 새로 붙는다. 같은 이벤트가 (재)전달되고,
    # 이번에는 실제로 성공했으니 그제서야 발행 표시가 찍힌다.
    good_client = FakeWebSocket(fail=False)
    clients.add(good_client)

    last_id = await pump_once(maker, clients, last_id)

    assert last_id == rows[0].event_id
    assert len(good_client.received) == 1
    assert good_client.received[0]["event_type"] == "task_submitted"
    assert good_client.received[0]["aggregate_id"] == "t-window"

    async with maker() as s:
        rows_after = (await s.execute(select(OutboxEvent))).scalars().all()
    assert rows_after[0].published_at is not None


async def test_no_clients_skips_cycle_without_touching_db(maker) -> None:
    async with maker() as s:
        s.add(
            OutboxEvent(
                aggregate="task",
                aggregate_id="t-idle",
                event_type="task_submitted",
                payload={"agent": "dev"},
            )
        )
        await s.commit()

    last_id = await pump_once(maker, set(), 0)

    assert last_id == 0
    async with maker() as s:
        rows = (await s.execute(select(OutboxEvent))).scalars().all()
    assert rows[0].published_at is None
