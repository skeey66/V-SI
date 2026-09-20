from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.models import OutboxEvent

DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


@pytest_asyncio.fixture
async def session_maker():
    """실제 Postgres에 붙는 세션메이커 (`tests/integration/harness.py`의
    `db_session()` 과 같은 연결 관행을 따른다).

    이 `events` 테이블은 오케스트레이터·이벤트게이트웨이가 상시로 같이 쓰는
    실 테이블이라, `tests/conftest.py`의 `session` 픽스처처럼 drop_all 로
    비웠다 되돌릴 수 없다(그 사이 다른 프로세스가 죽는다). 그래서 시작 시
    테이블이 비어 있다고 가정하지 않고, 테스트 시작 시점의 최대 `event_id`를
    워터마크로 잡아 두었다가, 끝나면 그 이후에 생긴 행만 지운다 — 기존 행에는
    손대지 않고, 이 테스트가 새로 쓴 행만 정리한다.
    """
    engine = make_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        watermark = (await s.execute(select(func.max(OutboxEvent.event_id)))).scalar() or 0
    try:
        yield maker
    finally:
        async with maker() as s:
            await s.execute(delete(OutboxEvent).where(OutboxEvent.event_id > watermark))
            await s.commit()
        await engine.dispose()
