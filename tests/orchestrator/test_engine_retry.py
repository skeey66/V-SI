"""`dispatch_agent`의 `submit()`-실패 경로에 재시도 정책(Task 12)을 고정한다.

여기서 시험하는 것은 **에이전트 실행 자체가 아니라 제출 왕복이 터지는 경우**다
(연결 거부, 5xx, 스키마 오류 등) — Task 13이 다루는 "제출은 됐는데 executor가
크래시해 푸시가 안 오는" 경로(리컨실러의 PROBE)와는 다른 층이다. 여기 실패는
아직 에이전트 쪽에 Task가 생기지 않았으므로 같은 멱등성 키로 그 자리에서
다시 시도해도 안전하다 — 새 `WorkflowTask` 행을 만들 필요가 없다.

가짜 `AgentClient`로 `submit()`이 몇 번째에 성공/포기하는지를 결정론적으로
고정한다. `asyncio.sleep`은 실제로 기다리지 않도록 패치한다 — 정책의 존재를
시험하는 것이지 시계를 시험하는 것이 아니다.
"""

from __future__ import annotations

import os

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator import engine as engine_module
from orchestrator.a2a_client import TaskSnapshot
from orchestrator.db import make_engine
from orchestrator.engine import TASK_FAILED, TASK_WORKING, WorkflowEngine
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.retry import FailureClass
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


class FakeAgentClient:
    """`submit()`이 지정한 예외를 몇 번 던지다 성공하는 이중.

    `get_task`는 refresh_task의 "디스패치 직후 안전망" 조회를 만족시키기 위한
    것으로, 아직 끝나지 않았다고만 답한다(working) — 이 테스트가 보는 것은
    제출 재시도지 완료 처리 경로가 아니다.
    """

    def __init__(self, failures: list[Exception], a2a_id: str = "a2a-1") -> None:
        self._failures = list(failures)
        self._a2a_id = a2a_id
        self.submit_calls = 0

    async def submit(self, payload: dict, key: str) -> str:
        self.submit_calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return self._a2a_id

    async def get_task(self, a2a_task_id: str) -> TaskSnapshot:
        return TaskSnapshot(a2a_task_id=a2a_task_id, state="TASK_STATE_WORKING")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """백오프 정책이 있는지는 시험하되, 테스트가 실제로 기다리지는 않는다."""

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(engine_module.asyncio, "sleep", _fast_sleep)


async def _engine_with_client(requirement_id: str, agent: str, client, session):
    session.add(
        WorkflowRequirement(
            requirement_id=requirement_id,
            title="회원가입",
            state=RequirementState.PLANNED.value,
            revision=1,
            run_id=f"run-{requirement_id}",
        )
    )
    await session.commit()
    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = WorkflowEngine(
        maker, {agent: client}, TimeoutConfig.from_env({})
    )
    return db, maker, workflow


async def _only_task(maker, requirement_id: str) -> WorkflowTask:
    async with maker() as s:
        rows = (
            await s.execute(
                select(WorkflowTask).where(
                    WorkflowTask.requirement_id == requirement_id
                )
            )
        ).scalars().all()
        assert len(rows) == 1, f"행이 정확히 하나여야 한다: {rows}"
        return rows[0]


def _500() -> httpx.HTTPStatusError:
    resp = httpx.Response(500, request=httpx.Request("POST", "http://x"))
    return httpx.HTTPStatusError("boom", request=resp.request, response=resp)


async def test_poison_submit_failure_is_not_retried(session) -> None:
    """422급 실패(POISON)는 첫 시도에서 바로 포기한다 — 재시도해도 결과가 같다."""
    rid = "REQ-RETRY-POISON"
    resp = httpx.Response(422, request=httpx.Request("POST", "http://x"))
    exc = httpx.HTTPStatusError("bad", request=resp.request, response=resp)
    client = FakeAgentClient([exc, exc, exc])  # 재시도됐다면 이만큼 더 던졌을 것
    db, maker, workflow = await _engine_with_client(rid, "planner", client, session)
    try:
        await workflow.dispatch_agent(rid, "planner")  # 터지지 않는다
        assert client.submit_calls == 1
        task = await _only_task(maker, rid)
        assert task.state == TASK_FAILED
        assert task.failure_class == FailureClass.POISON.value
        assert task.a2a_task_id is None
    finally:
        await db.dispose()


async def test_transport_submit_failure_retries_then_succeeds(session) -> None:
    """5xx급 실패(TRANSPORT)는 최대 3회 시도 안에서 성공하면 살아난다."""
    rid = "REQ-RETRY-TRANSPORT-OK"
    client = FakeAgentClient([_500(), _500()], a2a_id="a2a-recovered")
    db, maker, workflow = await _engine_with_client(rid, "planner", client, session)
    try:
        await workflow.dispatch_agent(rid, "planner")
        assert client.submit_calls == 3
        task = await _only_task(maker, rid)
        assert task.state == TASK_WORKING
        assert task.a2a_task_id == "a2a-recovered"
        assert task.failure_class is None
    finally:
        await db.dispose()


async def test_transport_submit_failure_gives_up_after_max_attempts(session) -> None:
    """3회 다 실패하면(TRANSPORT 상한) 그 행은 실패로 확정되고 더 시도하지 않는다."""
    rid = "REQ-RETRY-TRANSPORT-FAIL"
    client = FakeAgentClient([_500(), _500(), _500(), _500()])
    db, maker, workflow = await _engine_with_client(rid, "planner", client, session)
    try:
        await workflow.dispatch_agent(rid, "planner")
        assert client.submit_calls == 3  # 4번째는 없다 — 상한을 지킨다
        task = await _only_task(maker, rid)
        assert task.state == TASK_FAILED
        assert task.failure_class == FailureClass.TRANSPORT.value
        assert task.a2a_task_id is None
    finally:
        await db.dispose()
