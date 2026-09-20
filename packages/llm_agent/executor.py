"""A2A `AgentExecutor` 구현. SP1 의 `StubExecutor` 자리에 들어간다.

SP1 의 이벤트 순서 규칙을 그대로 지킨다 — **Task 이벤트를 상태 갱신보다 먼저**
큐에 넣어야 한다. SDK 의 `TaskManager` 는 Task 가 저장되기 전에 도착한
`TaskStatusUpdateEvent` 를 `InvalidAgentResponseError` 로 거절한다
(`a2a/server/agent_execution/active_task.py`, `packages/stub_agent/executor.py`
가 이미 이 순서를 지키고 있다).

`LoopFailed` 는 잡지 않고 전파한다. SDK 는 `execute()` 의 처리되지 않은 예외를
`TASK_STATE_FAILED` 로 바꾼다(실제로 설치된 SDK 에는 `TASK_STATE_ERROR` 라는
상태가 없다 — `TaskState` 에 정의된 값은 SUBMITTED/WORKING/COMPLETED/FAILED/
CANCELED/REJECTED/INPUT_REQUIRED/AUTH_REQUIRED 뿐이다). 아티팩트 없이
FAILED 로 끝나면 오케스트레이터는 EXECUTION 실패로 분류해 재시도·환류 상한을
이어받는다(스펙 §10).

**브리프(Task 9 계획) 대비 실제 인터페이스 차이, 전부 실측 확인:**

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
4. `mcp.client.streamable_http` 의 공개 함수 이름은 `streamablehttp_client` 가
   아니라 `streamable_http_client` 이고, 그 컨텍스트매니저는
   `(read_stream, write_stream)` 2-튜플만 낸다 — 구버전 SDK 의 3-튜플
   `(read, write, get_session_id)` 패턴이 아니다.

**리뷰 라운드 1 에서 추가된 것 (교차 참조: `docs/plans` 없음, 이 파일 안에서
새로 판단):**

- **`mcp_url` 이 실제로 이 역할의 도구를 광고하는지 확인한다.** 잘못된 마운트
  경로(예: `dev` 가 `/mcp/qa/` 를 바라봄)로도 `ToolBridge` 의 허용 목록 검사가
  권한 상승은 막아준다 — `tool_schemas`/`ToolBridge` 둘 다 `role.tools` 로
  걸러서 구성되므로, 잘못 연결된 dev 가 `run_tests` 를 부를 수는 없다. 진짜
  위험은 그게 아니라: 서버가 광고하지 않는 이름은 `tool_schemas` 가 조용히
  건너뛰므로(`mcp_client.py`), 잘못 연결되면 스키마가 **비거나 모자란 채로**
  모델에게 간다 — 모델은 도구를 한 번도 못(또는 일부만) 부르고 한 턴 만에
  끝내고, `loop.py` 는 (검증자가 아니면) `exit_code = 0` 을 찍는다.
  오케스트레이터 눈에는 "dev 가 성공적으로 끝냈다"로 보이지만 실제로는 코드를
  한 줄도 못 썼다 — 성공으로 위장한 무동작. 그래서 도구 목록을 받은 직후,
  `role.tools` 가 전부 스키마에 있는지 확인하고 없으면 `McpRoleMismatch` 로
  요란하게 죽는다. 시끄러운 기동 실패가 조용한 성공보다 낫다.
- **자문 이벤트 기록 실패가 에이전트 실행을 끝내면 안 된다.** Task 8 은
  `on_tool` 을 인라인 `await` 로 부르기로 했다(백그라운드 `create_task` 의
  가비지 컬렉션 위험을 피하려고) — 그 판단은 유지한다. 하지만 `loop.py` 는
  `on_tool` 을 무조건 `await` 만 할 뿐 그 실패를 흡수하지 않는다(그건
  `loop.py` 의 책임이 아니다 — 그 파일은 `on_tool` 이 무엇을 하든 상관하지
  않는 일반 계약만 진다). "이 이벤트는 워크플로 권위가 없다"(`events.py`,
  스펙 §8.1)는 지식은 `record_tool_event` 를 실제로 호출하는 여기(executor)
  에 있으므로, 가드도 여기에 둔다 — `loop.py` 를 건드리지 않는다. Postgres
  순단 하나가 도구 호출 상한(최대 90초)이나 턴 전체를 태우는 사고를 막는다.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack

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

logger = logging.getLogger(__name__)


#: MCP 연결 절차(스트림 연결 + 세션 진입 + `initialize` + `list_tools`) 전체에
#: 거는 상한(초). `run_loop`의 600초 예산은 이 절차가 끝난 뒤에야 시작하므로
#: (아래 `run_task`에서 이 블록을 통과해야 `run_loop`를 부른다), 여기에
#: 상한이 없으면 에이전트 총 wall-clock이 `연결 대기(무한) + 600초`가 되어
#: 오케스트레이터의 900초 천장(스펙 §9.2 — "죽은 프로세스만 잡는다")이
#: "워크스페이스 컨테이너가 느리게 뜨는 것"과 "MCP 서버가 죽었거나 URL이
#: 잘못됐다"를 구분할 수 없게 만든다.
#:
#: `workspace` 서비스(`docker-compose.yml`)에는 헬스체크도 `depends_on`
#: 게이트도 없다 — 에이전트가 그보다 먼저 뜰 수 있고, 잘못된 URL이면 TCP
#: 연결 자체가 (즉시 거부되지 않고) 리눅스 기본 SYN 재시도 상한(2분 안팎)
#: 까지 매달릴 수 있다. 반면 연결에 성공한 뒤의 `initialize`/`list_tools`는
#: 로컬 컨테이너 간 왕복 한두 번이라 정상적으로는 수백 ms 안에 끝난다.
#: 60초로 잡는다 — 이미지 빌드는 끝난 뒤 컨테이너 부팅(uvicorn 기동)만
#: 남은 정상적인 "느린 시작"은 여유 있게 넘기면서, 오케스트레이터의 900초
#: 천장에 240초(= 900 - 600 - 60)의 넉넉한 여유를 남겨 이 타임아웃 자체가
#: 그 천장보다 먼저, 더 구체적인 원인으로 확정 실패한다.
MCP_CONNECT_TIMEOUT_S = 60.0


class McpConnectTimeout(RuntimeError):
    """MCP 연결 절차(연결·`initialize`·`list_tools`)가 시간 안에 끝나지 않았다.

    이 실패는 `run_loop`의 600초 예산 *앞*에서 난다 — 그 예산이 감당할 범위가
    아니다. 오케스트레이터에는 `McpRoleMismatch`처럼 잡히지 않고 그대로
    전파되어 `execute()`가 `TASK_STATE_FAILED`로 끝낸다.
    """


class McpRoleMismatch(RuntimeError):
    """`mcp_url` 이 이 역할에 필요한 도구를 다 광고하지 않는다.

    잘못된 역할 경로로 연결됐다는 뜻이다(예: dev 가 `/mcp/qa/` 를 바라봄).
    권한 상승은 `ToolBridge`/`tool_schemas` 의 허용 목록 검사가 이미 막지만,
    이 상황을 그냥 두면 도구가 없는 채로 모델이 한 턴 만에 끝내
    "성공"(`exit_code=0`)으로 기록될 수 있다 — 그래서 조용히 넘어가지 않고
    여기서 죽는다.
    """


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
        requirement_id = payload.get("requirement_id")
        if not requirement_id:
            # 그냥 `payload["requirement_id"]` 로 두면 `KeyError:
            # 'requirement_id'` 뿐이라 진단이 얇다 — 무엇이 왔는지(빈 dict?
            # 다른 키만 있는 dict?)를 에러 메시지에 남긴다.
            raise ValueError(
                f"디스패치 페이로드에 'requirement_id' 가 없다: {payload!r}"
            )
        title = payload.get("title") or requirement_id
        revision = int(payload.get("revision") or 1)
        feedback = payload.get("feedback") or []

        async def on_tool(name: str, args: dict, result: dict) -> None:
            # `run_loop` 이 이 콜백을 무조건 `await` 한다 — 그러니 `async def`
            # 로 정의해서 그 계약을 구조적으로 만족한다(동기 함수 + 백그라운드
            # `create_task` 조합은 태스크 참조 유실로 이벤트를 잃을 수 있다).
            if self._session_maker is None:
                return
            try:
                await record_tool_event(
                    self._session_maker, requirement_id=requirement_id,
                    agent=self.role.name, revision=revision, tool=name, result=result,
                )
            except Exception:
                # 자문 이벤트는 워크플로 권위가 없다(`events.py`, 스펙 §8.1) —
                # 이 기록이 실패해도 에이전트 실행 자체가 죽으면 안 된다.
                # 인라인 `await` (Task 8 의 판단)는 유지하고 실패만 여기서
                # 삼킨다.
                logger.exception(
                    "자문 이벤트 기록 실패 — 에이전트 실행은 계속한다 "
                    "(requirement_id=%s, tool=%s)", requirement_id, name,
                )

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as http:
            llm = OllamaClient(self._ollama_url, self._model, http)
            # `mcp==2.2.0` 의 `streamable_http_client` 는 2-튜플만 낸다
            # (`read_stream`, `write_stream`) — 브리프가 가정한 3-튜플
            # `(read, write, _)`(구버전 SDK 패턴)이 아니다. 소스를 직접
            # 확인했다(`mcp/client/streamable_http.py`: `yield read_stream,
            # write_stream`).
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(MCP_CONNECT_TIMEOUT_S):
                        read, write = await stack.enter_async_context(
                            streamable_http_client(self._mcp_url)
                        )
                        session = await stack.enter_async_context(
                            ClientSession(read, write)
                        )
                        await session.initialize()
                        listed = await session.list_tools()
                except TimeoutError as exc:
                    raise McpConnectTimeout(
                        f"'{self.role.name}' 역할이 {self._mcp_url!r} 에 연결하는 "
                        f"절차(연결·initialize·list_tools)가 {MCP_CONNECT_TIMEOUT_S}초 "
                        "안에 끝나지 않았다 — 워크스페이스 컨테이너가 죽었거나 "
                        "URL이 잘못됐을 가능성이 높다."
                    ) from exc
                raw = [
                    {"name": t.name, "description": t.description or "",
                     "input_schema": t.input_schema or {}}
                    for t in listed.tools
                ]
                schemas = tool_schemas(self.role.tools, raw)
                schema_names = {s["function"]["name"] for s in schemas}
                missing = set(self.role.tools) - schema_names
                if missing:
                    raise McpRoleMismatch(
                        f"'{self.role.name}' 역할이 {self._mcp_url!r} 에 "
                        f"연결됐지만 그 서버는 이 역할에 필요한 도구를 "
                        f"다 광고하지 않는다 — 없는 도구: {sorted(missing)}. "
                        "잘못된 역할 경로로 연결됐을 가능성이 높다."
                    )
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
