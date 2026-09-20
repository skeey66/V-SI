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
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from llm_agent.executor import LlmExecutor, payload_to_part


class _Queue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


class _Ctx:
    def __init__(self, payload: dict) -> None:
        self.task_id = "t-1"
        self.context_id = "c-1"
        self.current_task = None
        self._payload = payload

    def get_user_input(self):  # 사용하지 않는다
        return ""


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
    # dev 에게 허용되지 않은 도구도 서버가 광고한다고 가정해도(권한은
    # tool_schemas/role.tools 가 거른다) 안전해야 한다.
    _FakeTool("run_tests", "돌린다", {"type": "object", "properties": {
        "requirement_id": {"type": "string"}}, "required": ["requirement_id"]}),
]


@pytest.fixture(autouse=True)
def _fake_mcp(monkeypatch):
    """MCP 왕복을 신경 쓰지 않는 테스트를 위한 기본 가짜.

    `run_task` 는 무조건 `streamable_http_client` 로 연결하고
    `session.initialize()` 까지 실행한다 — 패치 없이 두면 존재하지 않는
    호스트로 실제 TCP 연결을 시도해 모든 테스트가 네트워크 오류로 죽는다.
    MCP 자체가 관심사인 테스트(`test_mcp_session_uses_real_snake_case_attribute`)
    는 이 기본값을 자신의 `monkeypatch.setattr` 로 다시 덮어쓴다 — 같은
    `monkeypatch` 픽스처 인스턴스 안에서 나중 호출이 이긴다.
    """
    @asynccontextmanager
    async def fake_streamable_http_client(url):
        yield (None, None)

    def fake_client_session(read, write):
        return _FakeSession([])

    monkeypatch.setattr("llm_agent.executor.streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr("llm_agent.executor.ClientSession", fake_client_session)


async def test_dispatch_payload_fields_reach_the_loop(monkeypatch) -> None:
    """오케스트레이터가 넓힌 페이로드를 그대로 쓴다 (스펙 §4.3)."""
    seen = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/dev/", session_maker=None)
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
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/dev/", session_maker=None)
    await ex.run_task({"requirement_id": "REQ-1"})


async def test_loop_failure_propagates_so_sdk_marks_task_errored(monkeypatch) -> None:
    """LoopFailed 를 삼키지 않는다 — SDK 가 TASK_STATE_ERROR 로 바꾼다."""
    from llm_agent.loop import LoopFailed

    async def fake_loop(**kwargs):
        raise LoopFailed("턴 상한")

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/dev/", session_maker=None)
    with pytest.raises(LoopFailed):
        await ex.run_task({"requirement_id": "REQ-1"})


async def test_unknown_agent_is_a_configuration_error() -> None:
    with pytest.raises(KeyError):
        LlmExecutor(agent="wat", ollama_url="http://x", model="qwen3:8b",
                    mcp_url="http://y/mcp/wat/", session_maker=None)


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
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/dev/", session_maker=object())
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
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/dev/", session_maker=None)
    await ex.run_task({"requirement_id": "REQ-1"})
    assert called is False


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

    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                      mcp_url="http://y/mcp/dev/", session_maker=None)
    result_payload = await ex.run_task({"requirement_id": "REQ-1"})

    assert fake_session.initialized is True
    assert result_payload == {"kind": "source_code", "agent": "dev", "exit_code": 0}

    schema_names = {s["function"]["name"] for s in captured["schemas"]}
    # dev 는 write_file/list_files/read_file 만 봐야 한다 — run_tests 는 서버가
    # 광고해도 role.tools 로 걸러진다.
    assert schema_names == {"list_files", "read_file", "write_file"}
    for schema in captured["schemas"]:
        assert "requirement_id" not in schema["function"]["parameters"]["properties"]

    bridge = captured["bridge"]
    assert bridge._session is fake_session
    assert bridge._requirement_id == "REQ-1"
    assert bridge._allowed == frozenset({"list_files", "read_file", "write_file"})

    assert captured["role"].name == "dev"


def test_payload_to_part_round_trips_a_dict() -> None:
    from google.protobuf.json_format import MessageToDict

    payload = {"kind": "source_code", "agent": "dev", "exit_code": 0, "summary": "됐다"}
    part = payload_to_part(payload)
    assert MessageToDict(part.data) == payload
