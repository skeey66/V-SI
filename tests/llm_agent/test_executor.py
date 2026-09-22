"""`LlmExecutor` — A2A 경계.

브리프(`task-9-brief.md`)의 스케치는 실제 인터페이스와 여러 군데 어긋난다
(전부 실측 확인):

1. MCP `Tool` 객체는 `inputSchema` 가 아니라 `input_schema`(스네이크케이스)를
   노출한다(`mcp_client.py` 참고).
2. `run_loop` 의 `on_tool` 은 `Awaitable[None]` 계약이다 — 동기 함수를 넘기면
   루프가 `await None` 을 해서 `TypeError` 로 죽는다. `asyncio.create_task` 로
   던지는 fire-and-forget 은 참조 유실로 이벤트가 조용히 사라질 수 있어 금지다.
3. MCP 서버는 역할별로 `/mcp/<role>/` 에 따로 마운트된다 — `mcp_url` 은
   생성자가 그대로 받아쓴다(엮는 것은 이 파일을 쓰는 쪽의 책임).
4. 설치된 mcp SDK 의 `mcp.client.streamable_http` 공개 함수 이름은
   `streamablehttp_client` 가 아니라 `streamable_http_client` 이고, 그
   컨텍스트매니저는 `(read_stream, write_stream)` 2-튜플만 낸다(브리프가
   가정한 3-튜플이 아니다) — 소스(`streamable_http.py`)를 직접 읽어 확인.

`run_task` 는 실제로 MCP 세션을 열려고 시도한다(`session.initialize()` 까지
포함) — 그래서 MCP 를 신경 쓰지 않는 테스트도 네트워크를 실제로 타지 않도록
`_fake_mcp` 오토유즈 픽스처로 기본 가짜 왕복을 심어 둔다. MCP 자체를
검사하는 테스트는 자신의 monkeypatch 로 이 기본값을 덮어쓴다.

**리뷰 라운드 1 추가분**: 1차 제출은 `run_task` 와 `payload_to_part` 만
시험하고 `execute()`/`cancel()`/`_incoming_payload` 는 전혀 건드리지 않았다
(`_Queue`/`_Ctx` 가 죽은 코드로 남아 있던 것이 그 증거였다). 이번 라운드는
A2A 경계 자체 — 이벤트 순서, 취소 종단 상태, 페이로드 추출 — 를 시험하고,
MCP 역할 불일치 조기 실패와 자문 이벤트 실패 흡수도 추가한다.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
from a2a.helpers import new_data_part
from a2a.types import Message, Role, Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent

from llm_agent.executor import LlmExecutor, McpRoleMismatch, _incoming_payload, payload_to_part


class _Queue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


class _Ctx:
    """`RequestContext` 를 오리덕타이핑한다 — `execute`/`cancel`/
    `_incoming_payload` 가 실제로 읽는 속성만 담는다."""

    def __init__(self, *, message=None, current_task=None,
                 task_id: str = "t-1", context_id: str = "c-1") -> None:
        self.task_id = task_id
        self.context_id = context_id
        self.current_task = current_task
        self.message = message


def _new_executor(*, agent: str = "dev", session_maker=None) -> LlmExecutor:
    return LlmExecutor(agent=agent, ollama_url="http://x", model="qwen3:8b",
                        mcp_url=f"http://y/mcp/{agent}/", session_maker=session_maker)


class _FakeTool:
    """실제 mcp SDK `Tool` 을 흉내 낸다 — `input_schema`(스네이크케이스)만
    노출한다. `inputSchema` 속성은 아예 없다: 구현이 `t.inputSchema` 를 읽으면
    (브리프의 코드가 그렇다) 여기서 `AttributeError` 로 터져야 한다."""

    def __init__(self, name: str, description: str, input_schema: dict) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema


class _FakeListToolsResult:
    def __init__(self, tools) -> None:
        self.tools = tools


class _FakeSession:
    def __init__(self, tools) -> None:
        self._tools = tools
        self.initialized = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self):
        return _FakeListToolsResult(self._tools)


DEV_TOOLS = [
    _FakeTool("list_files", "나열", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}}, "required": ["requirement_id"]}),
    _FakeTool("read_file", "읽는다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}, "path": {"type": "string"}},
        "required": ["requirement_id", "path"]}),
    _FakeTool("write_file", "쓴다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}, "path": {"type": "string"},
        "content": {"type": "string"}},
        "required": ["requirement_id", "path", "content"]}),
    _FakeTool("run_lint", "훑는다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}}, "required": ["requirement_id"]}),
    # dev 에게 허용되지 않은 도구도 서버가 광고한다고 가정해도(권한은
    # tool_schemas/role.tools 가 거른다) 안전해야 한다.
    _FakeTool("run_tests", "돌린다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}}, "required": ["requirement_id"]}),
]

# qa 의 MCP 서버를 흉내 낸다 — dev 가 실수로 여기 연결됐다고 가정한다.
# `write_file` 이 없다: dev 에게 필수인 도구가 이 서버에는 없다.
QA_TOOLS = [
    _FakeTool("list_files", "나열", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}}, "required": ["requirement_id"]}),
    _FakeTool("read_file", "읽는다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}, "path": {"type": "string"}},
        "required": ["requirement_id", "path"]}),
    _FakeTool("run_tests", "돌린다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}}, "required": ["requirement_id"]}),
]


@pytest.fixture(autouse=True)
def _fake_mcp(monkeypatch):
    """MCP 왕복을 신경 쓰지 않는 테스트를 위한 기본 가짜.

    `run_task` 는 무조건 `streamable_http_client` 로 연결하고
    `session.initialize()` 까지 실행한다 — 패치 없이 두면 존재하지 않는
    호스트로 실제 TCP 연결을 시도해 모든 테스트가 네트워크 오류로 죽는다.
    MCP 자체가 관심사인 테스트는 이 기본값을 자신의 `monkeypatch.setattr` 로
    다시 덮어쓴다 — 같은 `monkeypatch` 픽스처 인스턴스 안에서 나중 호출이
    이긴다. 기본 가짜는 `DEV_TOOLS` 를 광고해서, `dev` 역할을 쓰는 대부분의
    테스트가 새로 추가된 역할-불일치 검사에 걸리지 않게 한다.
    """
    @asynccontextmanager
    async def fake_streamable_http_client(url):
        yield (None, None)

    def fake_client_session(read, write):
        return _FakeSession(DEV_TOOLS)

    monkeypatch.setattr("llm_agent.executor.streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr("llm_agent.executor.ClientSession", fake_client_session)


# ---------------------------------------------------------------------------
# run_task: 디스패치 필드 전달, 실패 전파, 설정 오류
# ---------------------------------------------------------------------------

async def test_dispatch_payload_fields_reach_the_loop(monkeypatch) -> None:
    """오케스트레이터가 넓힌 페이로드를 그대로 쓴다 (스펙 §4.3)."""
    seen = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = _new_executor()
    await ex.run_task({"requirement_id": "REQ-1", "title": "계산기",
                        "revision": 2, "feedback": [{"agent": "qa", "verdict": "FAIL",
                                                      "summary": "틀렸다"}]})
    assert seen["title"] == "계산기"
    assert seen["revision"] == 2
    assert seen["feedback"][0]["summary"] == "틀렸다"


async def test_missing_title_is_not_fatal(monkeypatch) -> None:
    """SP1 형식 페이로드(requirement_id 만)로도 죽지 않는다.

    실제로 살아있는 compose 스택에 대고 `POST /requirements` 를 던져 확인한
    결과, 오케스트레이터가 현재 dev 에이전트에 보내는 페이로드는 정확히
    `{"requirement_id": "..."}` 뿐이다(title/revision/feedback 없음) — 이
    테스트가 상상이 아니라 실제 운영 경로다.
    """
    async def fake_loop(**kwargs):
        assert kwargs["title"] == "REQ-1"
        assert kwargs["revision"] == 1
        assert kwargs["feedback"] == []
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = _new_executor()
    await ex.run_task({"requirement_id": "REQ-1"})


async def test_missing_requirement_id_raises_a_named_error() -> None:
    """`payload["requirement_id"]` 였다면 `KeyError: 'requirement_id'` 뿐이라
    진단이 얇다 — 무엇이 왔는지가 에러 메시지에 남아야 한다."""
    ex = _new_executor()
    with pytest.raises(ValueError, match="requirement_id"):
        await ex.run_task({"title": "제목만 있고 requirement_id 는 없다"})


async def test_loop_failure_propagates_so_sdk_marks_task_errored(monkeypatch) -> None:
    """LoopFailed 를 삼키지 않는다 — SDK 가 TASK_STATE_FAILED 로 바꾼다."""
    from llm_agent.loop import LoopFailed

    async def fake_loop(**kwargs):
        raise LoopFailed("턴 상한")

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = _new_executor()
    with pytest.raises(LoopFailed):
        await ex.run_task({"requirement_id": "REQ-1"})


async def test_unknown_agent_is_a_configuration_error() -> None:
    with pytest.raises(KeyError):
        LlmExecutor(agent="wat", ollama_url="http://x", model="qwen3:8b",
                    mcp_url="http://y/mcp/wat/", session_maker=None)


# ---------------------------------------------------------------------------
# on_tool: awaitable 계약, 자문 이벤트 실패 흡수
# ---------------------------------------------------------------------------

async def test_on_tool_is_an_awaitable_callable_not_fire_and_forget(monkeypatch) -> None:
    """`run_loop` 은 `on_tool` 을 무조건 `await` 한다(`loop.py` 의
    "여기서 무조건 `await` 해서 그 실패 모드를 구조적으로 없앤다" 대목).

    동기 함수를 넘기면 `await None` 이 `TypeError` 로 즉시 터진다. 이 테스트는
    가짜 `run_loop` 가 실제 루프처럼 `on_tool` 을 `await` 하도록 흉내 내서,
    executor 가 넘기는 콜백이 그 계약을 만족하는지 실측한다.
    """
    recorded = []

    async def fake_record_tool_event(session_maker, *, requirement_id, agent, revision, tool, result):
        recorded.append((requirement_id, agent, revision, tool, result))

    monkeypatch.setattr("llm_agent.executor.record_tool_event", fake_record_tool_event)

    async def fake_loop(**kwargs):
        on_tool = kwargs["on_tool"]
        # 루프가 실제로 하는 것과 동일하게: 무조건 await 한다.
        await on_tool("write_file", {"path": "a.py"}, {"ok": True, "detail": "done"})
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = _new_executor(session_maker=object())
    await ex.run_task({"requirement_id": "REQ-9", "revision": 3})

    assert recorded == [("REQ-9", "dev", 3, "write_file", {"ok": True, "detail": "done"})]


async def test_on_tool_is_a_safe_noop_without_a_session_maker(monkeypatch) -> None:
    """`session_maker=None`(단위 테스트/부트스트랩)이면 이벤트를 그냥 버린다.

    핵심은 여전히 *await 가능해야 한다* 는 것이다 — `None` 을 그냥 반환하는
    동기 함수였다면 이 테스트가 `TypeError` 로 실패했을 것이다.
    """
    called = False

    async def fake_record_tool_event(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("llm_agent.executor.record_tool_event", fake_record_tool_event)

    async def fake_loop(**kwargs):
        await kwargs["on_tool"]("write_file", {}, {"ok": True})
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = _new_executor()
    await ex.run_task({"requirement_id": "REQ-1"})
    assert called is False


async def test_on_tool_failure_is_logged_and_swallowed_not_fatal(monkeypatch, caplog) -> None:
    """자문 이벤트는 워크플로 권위가 없다(스펙 §8.1) — DB 순단 하나가 에이전트
    실행 전체를 끝내면 안 된다. Task 8 의 인라인 `await` 판단은 유지하면서
    실패만 흡수하는지 확인한다."""
    async def boom(*args, **kwargs):
        raise RuntimeError("db 순단")

    monkeypatch.setattr("llm_agent.executor.record_tool_event", boom)

    async def fake_loop(**kwargs):
        # 여기서 예외가 새어 나오면(흡수가 안 되면) 테스트가 실패한다.
        await kwargs["on_tool"]("write_file", {}, {"ok": True})
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = _new_executor(session_maker=object())

    with caplog.at_level(logging.ERROR, logger="llm_agent.executor"):
        payload = await ex.run_task({"requirement_id": "REQ-1"})

    assert payload == {"kind": "source_code", "agent": "dev", "exit_code": 0}
    assert any("자문 이벤트" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# MCP 역할 불일치: 조용한 성공이 아니라 시끄러운 설정 오류
# ---------------------------------------------------------------------------

async def test_mcp_session_uses_real_snake_case_attribute(monkeypatch) -> None:
    """`t.inputSchema` 였다면 이 테스트가 `AttributeError` 로 실패한다."""
    captured = {}

    @asynccontextmanager
    async def fake_streamable_http_client(url):
        assert url == "http://y/mcp/dev/"
        yield (None, None)  # 실제 SDK 는 2-튜플만 낸다 — 3-튜플이 아니다

    fake_session = _FakeSession(DEV_TOOLS)

    def fake_client_session(read, write):
        return fake_session

    async def fake_loop(**kwargs):
        captured.update(kwargs)
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr("llm_agent.executor.ClientSession", fake_client_session)
    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)

    ex = _new_executor()
    result_payload = await ex.run_task({"requirement_id": "REQ-1"})

    assert fake_session.initialized is True
    assert result_payload == {"kind": "source_code", "agent": "dev", "exit_code": 0}

    schema_names = {s["function"]["name"] for s in captured["schemas"]}
    # dev 가 보는 것은 role.tools 에 있는 것뿐이다 — run_tests 는 서버가
    # 광고해도 걸러진다(스펙 §5.4: 개발에게 검증 권한을 주지 않는다).
    # run_lint 는 판정 도구가 아니라 개발이 자기 코드를 훑는 도구라 있다.
    assert schema_names == {"list_files", "read_file", "write_file", "run_lint"}
    assert "run_tests" not in schema_names
    for schema in captured["schemas"]:
        assert "requirement_id" not in schema["function"]["parameters"]["properties"]

    bridge = captured["bridge"]
    assert bridge._session is fake_session
    assert bridge._requirement_id == "REQ-1"
    assert bridge._allowed == frozenset(
        {"list_files", "read_file", "write_file", "run_lint"}
    )

    assert captured["role"].name == "dev"


async def test_mcp_connect_is_bounded_by_a_timeout(monkeypatch) -> None:
    """`run_loop`의 600초 예산은 연결 절차가 끝난 뒤에야 시작한다 — 연결
    자체(스트림 연결·`initialize`·`list_tools`)에 상한이 없으면 총
    wall-clock 은 `연결 대기(무한) + 600초`가 되어, 오케스트레이터의 900초
    천장(스펙 §9.2, "죽은 프로세스만 잡는다")이 "워크스페이스가 느리게 뜬
    것"과 "MCP 서버가 죽은 것"을 구분하지 못하게 된다. 여기서는 연결 절차를
    아주 짧은 타임아웃으로 덮어써서, 멈춘 연결이 유한 시간 안에 확정
    실패로 끝난다는 것만 확인한다(실제 운영 값은 `llm_agent.executor` 모듈의
    상수가 결정한다)."""
    import asyncio

    from llm_agent import executor as executor_module

    @asynccontextmanager
    async def hanging_streamable_http_client(url):
        await asyncio.sleep(999)
        yield (None, None)  # pragma: no cover - 절대 도달하지 않는다

    monkeypatch.setattr(
        "llm_agent.executor.streamable_http_client", hanging_streamable_http_client
    )
    monkeypatch.setattr(executor_module, "MCP_CONNECT_TIMEOUT_S", 0.05)

    ex = _new_executor()
    started = asyncio.get_event_loop().time()
    with pytest.raises(executor_module.McpConnectTimeout) as exc_info:
        await ex.run_task({"requirement_id": "REQ-1"})
    elapsed = asyncio.get_event_loop().time() - started

    assert elapsed < 1.0
    assert "http://y/mcp/dev/" in str(exc_info.value)


async def test_mcp_endpoint_missing_required_tool_is_a_loud_configuration_error(monkeypatch) -> None:
    """dev 가 실수로 `/mcp/qa/` 에 연결되면(`write_file` 없음), 빈/모자란
    스키마로 조용히 넘어가면 안 된다.

    `mcp_client.py` 의 `tool_schemas` 는 서버가 광고하지 않는 이름을 조용히
    건너뛴다 — 그대로 두면 모델이 도구를 못 부르고 한 턴 만에 끝내고
    `loop.py` 가 `exit_code=0` 을 찍는다: 코드를 한 줄도 안 쓴 dev 태스크가
    오케스트레이터 눈에는 "성공"으로 보인다. 여기서 요란하게 죽어야 한다.
    """
    @asynccontextmanager
    async def fake_streamable_http_client(url):
        yield (None, None)

    def fake_client_session(read, write):
        return _FakeSession(QA_TOOLS)  # write_file 없음

    monkeypatch.setattr("llm_agent.executor.streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr("llm_agent.executor.ClientSession", fake_client_session)

    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/qa/", session_maker=None)

    with pytest.raises(McpRoleMismatch) as exc_info:
        await ex.run_task({"requirement_id": "REQ-1"})

    message = str(exc_info.value)
    assert "dev" in message
    assert "http://y/mcp/qa/" in message
    assert "write_file" in message


# ---------------------------------------------------------------------------
# execute(): SP1 이벤트 순서 규칙 — Task 이벤트가 상태 갱신보다 먼저
# ---------------------------------------------------------------------------

async def test_task_enqueued_before_any_status_update_when_no_current_task(monkeypatch) -> None:
    """SDK 의 `TaskManager` 는 Task 가 저장되기 전에 도착한
    `TaskStatusUpdateEvent` 를 `InvalidAgentResponseError` 로 거절한다 — 이
    순서가 깨지면 오케스트레이터가 폴링할 Task 자체가 없다."""
    async def fake_run_task(payload):
        return {"kind": "source_code", "agent": "dev", "exit_code": 0}

    ex = _new_executor()
    monkeypatch.setattr(ex, "run_task", fake_run_task)

    ctx = _Ctx(message=Message(message_id="m1", role=Role.ROLE_USER,
                                parts=[new_data_part({"requirement_id": "REQ-1"})]),
               current_task=None)
    queue = _Queue()
    await ex.execute(ctx, queue)

    assert isinstance(queue.events[0], Task)
    first_status_index = next(
        i for i, e in enumerate(queue.events) if isinstance(e, TaskStatusUpdateEvent)
    )
    assert first_status_index > 0


async def test_task_not_re_enqueued_when_current_task_already_set(monkeypatch) -> None:
    """Task 가 이미 저장돼 있으면(재시도·리컨실리에이션) 새 Task 를 다시
    큐에 넣지 않는다 — 그건 서버가 이미 아는 Task 다."""
    async def fake_run_task(payload):
        return {"kind": "source_code", "agent": "dev", "exit_code": 0}

    ex = _new_executor()
    monkeypatch.setattr(ex, "run_task", fake_run_task)

    ctx = _Ctx(message=Message(message_id="m1", role=Role.ROLE_USER,
                                parts=[new_data_part({"requirement_id": "REQ-1"})]),
               current_task=object())  # 존재 자체만 본다 — 타입은 상관없다
    queue = _Queue()
    await ex.execute(ctx, queue)

    assert not any(isinstance(e, Task) for e in queue.events)
    assert isinstance(queue.events[0], TaskStatusUpdateEvent)


async def test_artifact_precedes_failed_status_on_exit_code_nonzero(monkeypatch) -> None:
    """검증자 FAIL 이 크래시와 구별되려면 아티팩트가 종단 상태보다 먼저 큐에
    들어가야 한다 — `add_artifact` 가 `failed()` 뒤로 밀리거나 예외로
    바뀌면, 정상 완료된 FAIL 판정이 에이전트 크래시와 똑같아 보인다."""
    async def fake_run_task(payload):
        return {"kind": "test_report", "agent": "qa", "exit_code": 1, "verdict": "FAIL"}

    ex = _new_executor(agent="qa")
    monkeypatch.setattr(ex, "run_task", fake_run_task)

    ctx = _Ctx(message=Message(message_id="m1", role=Role.ROLE_USER,
                                parts=[new_data_part({"requirement_id": "REQ-1"})]),
               current_task=None)
    queue = _Queue()
    await ex.execute(ctx, queue)

    artifact_index = next(
        i for i, e in enumerate(queue.events) if isinstance(e, TaskArtifactUpdateEvent)
    )
    terminal_index, terminal_event = next(
        (i, e) for i, e in enumerate(queue.events)
        if isinstance(e, TaskStatusUpdateEvent)
        and e.status.state in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_COMPLETED)
    )
    assert artifact_index < terminal_index
    assert terminal_event.status.state == TaskState.TASK_STATE_FAILED


async def test_artifact_precedes_completed_status_on_exit_code_zero(monkeypatch) -> None:
    async def fake_run_task(payload):
        return {"kind": "source_code", "agent": "dev", "exit_code": 0}

    ex = _new_executor()
    monkeypatch.setattr(ex, "run_task", fake_run_task)

    ctx = _Ctx(message=Message(message_id="m1", role=Role.ROLE_USER,
                                parts=[new_data_part({"requirement_id": "REQ-1"})]),
               current_task=None)
    queue = _Queue()
    await ex.execute(ctx, queue)

    artifact_index = next(
        i for i, e in enumerate(queue.events) if isinstance(e, TaskArtifactUpdateEvent)
    )
    terminal_index, terminal_event = next(
        (i, e) for i, e in enumerate(queue.events)
        if isinstance(e, TaskStatusUpdateEvent)
        and e.status.state in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_COMPLETED)
    )
    assert artifact_index < terminal_index
    assert terminal_event.status.state == TaskState.TASK_STATE_COMPLETED


async def test_loop_failed_escapes_execute_after_task_and_working_are_enqueued(monkeypatch) -> None:
    """`LoopFailed` 는 `run_task` 뿐 아니라 `execute()` 자체를 벗어나야 한다 —
    SDK 가 신경 쓰는 계약은 `execute()` 의 처리되지 않은 예외지, `run_task`
    수준이 아니다. Task/WORKING 은 이미 큐에 들어간 뒤여야 오케스트레이터가
    폴링할 대상이 있다."""
    from llm_agent.loop import LoopFailed

    async def boom(payload):
        raise LoopFailed("턴 상한")

    ex = _new_executor()
    monkeypatch.setattr(ex, "run_task", boom)

    ctx = _Ctx(message=Message(message_id="m1", role=Role.ROLE_USER,
                                parts=[new_data_part({"requirement_id": "REQ-1"})]),
               current_task=None)
    queue = _Queue()
    with pytest.raises(LoopFailed):
        await ex.execute(ctx, queue)

    assert isinstance(queue.events[0], Task)
    assert isinstance(queue.events[1], TaskStatusUpdateEvent)
    assert queue.events[1].status.state == TaskState.TASK_STATE_WORKING
    assert not any(isinstance(e, TaskArtifactUpdateEvent) for e in queue.events)
    assert not any(
        isinstance(e, TaskStatusUpdateEvent)
        and e.status.state in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED)
        for e in queue.events
    )


# ---------------------------------------------------------------------------
# cancel(): CANCELED 다, FAILED 가 아니다
# ---------------------------------------------------------------------------

async def test_cancel_produces_canceled_not_failed() -> None:
    """브리프는 `updater.failed()` 를 스케치했지만 SP1 `StubExecutor.cancel`
    은 `updater.cancel()` 을 쓴다 — 취소 요청과 실행 실패는 다른 종단
    상태다."""
    ex = _new_executor()
    ctx = _Ctx(current_task=None)
    queue = _Queue()
    await ex.cancel(ctx, queue)

    assert len(queue.events) == 1
    assert isinstance(queue.events[0], TaskStatusUpdateEvent)
    assert queue.events[0].status.state == TaskState.TASK_STATE_CANCELED


# ---------------------------------------------------------------------------
# _incoming_payload: 실제 Message 객체로 확인한 경계 사례
# ---------------------------------------------------------------------------

def test_incoming_payload_empty_dict_when_message_is_none() -> None:
    assert _incoming_payload(_Ctx(message=None)) == {}


def test_incoming_payload_skips_scalar_data_parts() -> None:
    """data Part 의 값이 dict 가 아니면(스칼라) 건너뛴다 — `.update()` 를
    문자열에 부르면 죽는다."""
    message = Message(message_id="m1", role=Role.ROLE_USER,
                       parts=[new_data_part("그냥 문자열")])
    assert _incoming_payload(_Ctx(message=message)) == {}


def test_incoming_payload_merges_multiple_data_parts_last_wins() -> None:
    message = Message(
        message_id="m1", role=Role.ROLE_USER,
        parts=[
            new_data_part({"requirement_id": "REQ-1", "title": "old"}),
            new_data_part({"title": "new"}),
        ],
    )
    assert _incoming_payload(_Ctx(message=message)) == {
        "requirement_id": "REQ-1", "title": "new",
    }


def test_payload_to_part_round_trips_a_dict() -> None:
    from google.protobuf.json_format import MessageToDict

    payload = {"kind": "source_code", "agent": "dev", "exit_code": 0, "summary": "됐다"}
    part = payload_to_part(payload)
    assert MessageToDict(part.data) == payload
