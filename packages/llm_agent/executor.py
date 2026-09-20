"""A2A `AgentExecutor` 구현. SP1 의 `StubExecutor` 자리에 들어간다.

SP1 의 이벤트 순서 규칙을 그대로 지킨다 — **Task 이벤트를 상태 갱신보다 먼저**
큐에 넣어야 한다. SDK 의 `TaskManager` 는 Task 가 저장되기 전에 도착한
`TaskStatusUpdateEvent` 를 `InvalidAgentResponseError` 로 거절한다
(`a2a/server/agent_execution/active_task.py`, `packages/stub_agent/executor.py`
가 이미 이 순서를 지키고 있다).

`LoopFailed` 는 잡지 않고 전파한다. SDK 가 처리되지 않은 예외를
`TASK_STATE_ERROR` 로 바꾸고, 오케스트레이터의 재시도·환류 상한이 이어받는다
(스펙 §10).

**브리프(Task 9 계획) 대비 실제 인터페이스 차이 세 가지, 전부 실측 확인:**

1. MCP `Tool` 객체는 `inputSchema` 가 아니라 `input_schema`(스네이크케이스)를
   낸다 — 설치된 mcp SDK 를 `tests/llm_agent/test_mcp_client.py`/Task 5 리뷰가
   이미 확인했다. `tool_schemas` 는 두 철자를 다 받지만, 여기서 `dict` 를
   만들 때 실제 속성명을 써야 `AttributeError` 로 첫 도구 목록 조회에서
   죽지 않는다.
2. `run_loop` 의 `on_tool` 은 `Awaitable[None]` 계약이고 무조건 `await` 된다
   (`loop.py`). `asyncio.create_task` 로 fire-and-forget 하면 태스크 참조를
   아무 데도 붙잡아두지 않아 가비지 컬렉션으로 이벤트가 조용히 사라질 수
   있다 — 그래서 `on_tool` 자체를 `async def` 로 정의해 인라인으로 끝까지
   간다(`record_tool_event` 의 독스트링이 권하는 방식과 같다).
3. MCP 서버는 역할별로 `/mcp/<role>/` 에 따로 마운트된다
   (`services/tool_server/main.py`). 이 클래스는 role 을 URL 에 엮지 않는다 —
   생성자에 role 이 이미 반영된 `mcp_url` 을 그대로 받아 쓴다. 엮는 책임은
   이 클래스를 생성하는 쪽(Task 12 의 `build_executor`)에 있다.

추가로 실측한 것(브리프에는 없던 차이): `mcp.client.streamable_http` 의
공개 함수 이름은 `streamablehttp_client` 가 아니라 `streamable_http_client`
이고, 그 컨텍스트매니저는 `(read_stream, write_stream)` 2-튜플만 낸다 —
구버전 SDK 의 3-튜플 `(read, write, get_session_id)` 패턴이 아니다.
"""
from __future__ import annotations

import httpx
from a2a.helpers import get_data_parts, new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from llm_agent.events import record_tool_event
from llm_agent.loop import run_loop
from llm_agent.mcp_client import ToolBridge, tool_schemas
from llm_agent.ollama import OllamaClient
from llm_agent.roles import ROLES


def payload_to_part(payload: dict) -> Part:
    """dict를 A2A `Part`(protobuf `Value`)로 감싼다. SP1
    `stub_agent.executor._payload_to_part` 와 동일 동작이다."""
    value = struct_pb2.Value()
    ParseDict(payload, value)
    return Part(data=value)


class LlmExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        agent: str,
        ollama_url: str,
        model: str,
        mcp_url: str,
        session_maker,
    ) -> None:
        self.role = ROLES[agent]  # 알 수 없는 역할은 설정 오류다 — 여기서 터진다
        self._ollama_url = ollama_url
        self._model = model
        self._mcp_url = mcp_url
        self._session_maker = session_maker

    async def run_task(self, payload: dict) -> dict:
        """디스패치 페이로드를 받아 산출물 payload 를 돌려준다.

        `execute` 에서 분리해 둔 이유는 A2A 이벤트 배관 없이 단위 테스트할 수
        있게 하기 위해서다.
        """
        requirement_id = payload["requirement_id"]
        title = payload.get("title") or requirement_id
        revision = int(payload.get("revision") or 1)
        feedback = payload.get("feedback") or []

        async def on_tool(name: str, args: dict, result: dict) -> None:
            # `run_loop` 이 이 콜백을 무조건 `await` 한다 — 그러니 `async def`
            # 로 정의해서 그 계약을 구조적으로 만족한다(동기 함수 + 백그라운드
            # `create_task` 조합은 태스크 참조 유실로 이벤트를 잃을 수 있다).
            if self._session_maker is None:
                return
            await record_tool_event(
                self._session_maker, requirement_id=requirement_id,
                agent=self.role.name, revision=revision, tool=name, result=result,
            )

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as http:
            llm = OllamaClient(self._ollama_url, self._model, http)
            # `mcp==2.2.0` 의 `streamable_http_client` 는 2-튜플만 낸다
            # (`read_stream`, `write_stream`) — 브리프가 가정한 3-튜플
            # `(read, write, _)`(구버전 SDK 패턴)이 아니다. 소스를 직접
            # 확인했다(`mcp/client/streamable_http.py`: `yield read_stream,
            # write_stream`).
            async with streamable_http_client(self._mcp_url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    raw = [
                        {"name": t.name, "description": t.description or "",
                         "input_schema": t.input_schema or {}}
                        for t in listed.tools
                    ]
                    schemas = tool_schemas(self.role.tools, raw)
                    bridge = ToolBridge(session, requirement_id, self.role.tools)
                    result = await run_loop(
                        role=self.role, llm=llm, bridge=bridge, schemas=schemas,
                        title=title, revision=revision, feedback=feedback,
                        on_tool=on_tool,
                    )
        return result.payload

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    state=TaskState.TASK_STATE_SUBMITTED,
                )
            )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        payload = await self.run_task(_incoming_payload(context))
        await updater.add_artifact([payload_to_part(payload)], name=payload["kind"])
        if payload["exit_code"] == 0:
            await updater.complete()
        else:
            await updater.failed()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 브리프는 `updater.failed()` 를 스케치했지만, SP1 `StubExecutor.cancel`
        # 은 `updater.cancel()` 을 쓴다(TASK_STATE_CANCELED, FAILED 가 아니다) —
        # 취소 요청과 실행 실패는 다른 종단 상태이므로 SP1 관행을 따른다.
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def _incoming_payload(context: RequestContext) -> dict:
    """A2A 메시지의 data Part 에서 디스패치 페이로드를 꺼낸다.

    브리프는 `MessageToDict` 를 직접 손으로 돌리라고 했지만, 오케스트레이터
    쪽(`orchestrator/a2a_client.py`)이 보내는 쪽과 받는 쪽 모두 이미
    `a2a.helpers` 의 공개 헬퍼(`new_data_part`/`get_data_parts`)로 이 왕복을
    하고 있다 — 같은 대칭을 여기서도 쓴다. 실측: 살아있는 compose 스택에
    `POST /requirements` 를 던져 dev 컨테이너 로그로 확인한 실제 페이로드는
    `[{"requirement_id": "..."}]` 하나짜리 리스트였다(SP1 이 아직 title/
    revision/feedback 을 안 보낸다 — Task 10 이 그걸 넓힌다).
    """
    message = context.message
    if message is None:
        return {}
    merged: dict = {}
    for data in get_data_parts(message.parts):
        if isinstance(data, dict):
            merged.update(data)
    return merged
