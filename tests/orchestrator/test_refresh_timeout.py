"""멈춘 에이전트 하나가 리컨실리에이션 전체를 붙잡지 못하게 한다.

`reconcile_once`는 요구사항을 **직렬로** 훑는다. `refresh_task`가 응답 없는
에이전트를 무기한 기다리면 그 한 건이 나머지 모든 요구사항의 수렴을 함께
멈춰 세운다 — 공유 httpx 클라이언트의 타임아웃(단계 예산)이 유일한 상한이면
그 정지는 분 단위가 된다.

`submit()`은 이미 `wait_for(tool_s)`로 감싸여 있었다. 같은 층의 같은 왕복인
`get_task`만 감싸이지 않았던 것은 누락이지 설계가 아니다.
"""

from __future__ import annotations

import asyncio
import os
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.engine import TASK_FAILED, WorkflowEngine
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)

#: 부등식(`validate`)을 지키는 최소 설정. tool_s만 실질적으로 쓰인다.
FAST = TimeoutConfig(tool_s=1, executor_s=2, step_s=3, run_s=4)


class _HangingClient:
    """응답을 영원히 돌려주지 않는 에이전트."""

    def __init__(self) -> None:
        self.cancelled = False

    async def get_task(self, a2a_task_id: str):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_refresh_task_gives_up_on_a_hung_agent(session) -> None:
    """`tool_s`를 넘기면 기다림을 끊고, 그 행을 **모호한 실패**로 확정한다.

    타임아웃은 "에이전트가 작업을 받지 못했다"를 증명하지 못한다 — 그래서
    `submit()`의 모호한 실패와 같은 취급을 한다: 이 행은 여기서 끝내고,
    리컨실러가 새 행·새 멱등성 키로 다시 보낸다. 분류도 같은 `classify()`가
    내린다(내장 `TimeoutError` → TRANSPORT).
    """
    rid = "REQ-T-hang"
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회원가입",
            state=RequirementState.IMPLEMENTING.value, revision=1, run_id=f"run-{rid}",
        )
    )
    session.add(
        WorkflowTask(
            requirement_id=rid, agent="dev", revision=1, a2a_task_id=f"a2a-{rid}",
            idempotency_key=f"k-{rid}", state="working", attempt=1,
        )
    )
    await session.commit()

    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    client = _HangingClient()
    workflow = WorkflowEngine(maker, {"dev": client}, FAST)
    try:
        started = time.monotonic()
        # 바깥의 wait_for는 안전망일 뿐이다 — 엔진이 스스로 tool_s에 끊어야 한다.
        state = await asyncio.wait_for(
            workflow.refresh_task("dev", f"a2a-{rid}"), timeout=20
        )
        elapsed = time.monotonic() - started

        assert elapsed < 10, f"tool_s(1초)를 한참 넘겨 기다렸다: {elapsed:.1f}초"
        assert state == TASK_FAILED
        assert client.cancelled, "멈춘 왕복은 취소돼야 한다(연결을 붙잡고 있지 않는다)"

        async with maker() as s:
            row = (
                await s.execute(
                    select(WorkflowTask).where(WorkflowTask.requirement_id == rid)
                )
            ).scalar_one()
            assert row.state == TASK_FAILED
            assert row.failure_class == "transport"
    finally:
        await db.dispose()
