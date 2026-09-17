import os
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from orchestrator.db import make_engine
from orchestrator.models import Base

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


@pytest_asyncio.fixture
async def session():
    """단위 테스트용 빈 스키마.

    **테스트가 끝나면 반드시 비운다.** Task 13부터 오케스트레이터 컨테이너 안에서
    리컨실러가 상시로 돈다 — 단위 테스트가 남긴 비종료 요구사항 행(예:
    `test_rollback_discards_both`의 `planned` 상태 REQ-002)을 리컨실러가 정당한
    복구 대상으로 보고 **진짜 에이전트에 디스패치**한다(실측: 그 한 행 때문에
    planner→dev→qa→security 파이프라인이 통째로 한 번 더 돌았다). 스텁 에이전트의
    호출 카운터·verdict 커서가 소모되므로 통합 테스트의 결정성이 깨진다.

    비운 뒤 `create_all`로 되돌리는 것까지가 한 벌이다. 테이블이 사라진 채로
    남으면 컨테이너 쪽 리컨실러가 계속 예외를 뱉는다.
    """
    engine = make_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:
            yield s
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
