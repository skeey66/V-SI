from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
