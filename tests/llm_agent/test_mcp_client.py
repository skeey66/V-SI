import pytest

from llm_agent.mcp_client import ToolBridge, ToolNotAllowed, tool_schemas

RAW = [
    {"name": "write_file", "description": "쓴다",
     "inputSchema": {"type": "object",
                     "properties": {"requirement_id": {"type": "string"},
                                    "path": {"type": "string"},
                                    "content": {"type": "string"}},
                     "required": ["requirement_id", "path", "content"]}},
    {"name": "run_tests", "description": "돌린다",
     "inputSchema": {"type": "object",
                     "properties": {"requirement_id": {"type": "string"}},
                     "required": ["requirement_id"]}},
]

# 실제 mcp SDK 의 ClientSession.list_tools() 는 Tool 객체를 돌려주고, 그
# 객체의 파이썬 속성명은 카멜케이스 "inputSchema" 가 아니라 스네이크케이스
# "input_schema" 다. model_dump() (by_alias 없이) 를 쓰면 dict 도 같은
# 스네이크케이스 키로 나온다. tool_schemas 는 두 철자 모두 받아야 한다.
RAW_SNAKE_CASE = [
    {"name": "write_file", "description": "쓴다",
     "input_schema": {"type": "object",
                      "properties": {"requirement_id": {"type": "string"},
                                     "path": {"type": "string"}},
                      "required": ["requirement_id", "path"]}},
]


def test_requirement_id_is_stripped_from_what_the_model_sees() -> None:
    """모델이 워크스페이스를 지정할 수 없어야 한다 (스펙 §6.1)."""
    schemas = tool_schemas(["write_file"], RAW)
    params = schemas[0]["function"]["parameters"]
    assert "requirement_id" not in params["properties"]
    assert "requirement_id" not in params["required"]
    assert "path" in params["properties"]


def test_only_allowed_tools_are_exposed() -> None:
    schemas = tool_schemas(["write_file"], RAW)
    assert [s["function"]["name"] for s in schemas] == ["write_file"]


def test_tool_with_only_requirement_id_still_has_valid_schema() -> None:
    schemas = tool_schemas(["run_tests"], RAW)
    params = schemas[0]["function"]["parameters"]
    assert params["properties"] == {}
    assert params["required"] == []


def test_snake_case_input_schema_key_is_also_understood() -> None:
    """mcp SDK 의 Tool.model_dump() 는 기본적으로 input_schema 키를 준다."""
    schemas = tool_schemas(["write_file"], RAW_SNAKE_CASE)
    params = schemas[0]["function"]["parameters"]
    assert "requirement_id" not in params["properties"]
    assert "path" in params["properties"]


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return {"ok": True, "detail": "done"}


async def test_bridge_injects_requirement_id_on_call() -> None:
    session = _FakeSession()
    bridge = ToolBridge(session, "REQ-7", ["write_file"])
    await bridge.call("write_file", {"path": "a.py", "content": "x"})
    name, args = session.calls[0]
    assert name == "write_file"
    assert args["requirement_id"] == "REQ-7"
    assert args["path"] == "a.py"


async def test_model_supplied_requirement_id_is_overwritten() -> None:
    """모델이 인자를 지어내도 세션 값이 이긴다."""
    session = _FakeSession()
    bridge = ToolBridge(session, "REQ-7", ["write_file"])
    await bridge.call("write_file", {"requirement_id": "REQ-VICTIM", "path": "a.py", "content": "x"})
    assert session.calls[0][1]["requirement_id"] == "REQ-7"


async def test_tool_outside_allowlist_is_refused() -> None:
    bridge = ToolBridge(_FakeSession(), "REQ-7", ["read_file"])
    with pytest.raises(ToolNotAllowed):
        await bridge.call("run_tests", {})


async def test_tool_not_allowed_message_names_the_tool() -> None:
    """오류 메시지가 어떤 도구가 거부됐는지 알려줘야 한다 (말단 도구는
    루프 과제가 이 메시지를 모델에게 되돌려준다)."""
    bridge = ToolBridge(_FakeSession(), "REQ-7", ["read_file"])
    with pytest.raises(ToolNotAllowed, match="run_tests"):
        await bridge.call("run_tests", {})


async def test_malformed_tool_call_sentinel_is_rejected_like_any_other_unknown_tool() -> None:
    """Task 4 의 __malformed_tool_call__ 센티널은 특별 취급하지 않는다 —
    어떤 역할의 허용 목록에도 없으므로 그냥 거부된다."""
    bridge = ToolBridge(_FakeSession(), "REQ-7", ["write_file"])
    with pytest.raises(ToolNotAllowed, match="__malformed_tool_call__"):
        await bridge.call("__malformed_tool_call__", {"error": "boom", "raw": "x"})


class _FakeCallToolResult:
    """실제 mcp SDK 의 `CallToolResult` 모양을 흉내낸다 — dict 가 아니다."""

    def __init__(self, structured_content=None, content=None, is_error: bool = False) -> None:
        self.structured_content = structured_content
        self.content = content or []
        self.is_error = is_error


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _RealisticSession:
    """`call_tool` 이 dict 가 아니라 `CallToolResult` 를 돌려주는 세션."""

    def __init__(self, result) -> None:
        self._result = result
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self._result


async def test_call_tool_result_falls_back_to_parsing_json_text_when_structured_content_is_missing() -> None:
    """`structured_content` 가 없어도(예: 도구 함수 타입 표기가 `-> dict` 로
    되돌아간 경우) `content` 텍스트가 JSON 객체로 파싱되면 그것을 쓴다 —
    `exit_code` 를 잃지 않기 위한 방어선이다."""
    result = _FakeCallToolResult(
        structured_content=None,
        content=[_TextBlock('{"ok": false, "detail": "1 failed", "exit_code": 1}')],
        is_error=False,
    )
    bridge = ToolBridge(_RealisticSession(result), "REQ-7", ["run_tests"])
    out = await bridge.call("run_tests", {})
    assert out == {"ok": False, "detail": "1 failed", "exit_code": 1}


async def test_call_tool_result_without_structured_content_or_json_text_falls_back_to_is_error() -> None:
    """텍스트가 JSON 이 아니면(진짜 도구 크래시 등) `is_error` 로 최선의
    dict 를 만든다."""
    result = _FakeCallToolResult(structured_content=None, content=[_TextBlock("boom")], is_error=True)
    bridge = ToolBridge(_RealisticSession(result), "REQ-7", ["run_tests"])
    out = await bridge.call("run_tests", {})
    assert out == {"ok": False, "detail": "boom"}


async def test_call_tool_dict_passthrough_still_works() -> None:
    """단순 dict 를 돌려주는 테스트 더블(기존 _FakeSession)은 그대로 통과한다."""
    session = _FakeSession()
    bridge = ToolBridge(session, "REQ-7", ["write_file"])
    out = await bridge.call("write_file", {"path": "a.py", "content": "x"})
    assert out == {"ok": True, "detail": "done"}


# --- 실제 MCP 왕복(손으로 만든 더블이 아니라 진짜 MCPServer + ClientSession) ---
#
# 리뷰에서 지적된 함정: `_FakeCallToolResult` 로 만든 위 더블들은 속성 이름은
# 실제 `CallToolResult` 와 같지만, "`structured_content` 가 채워져 있다"는
# 전제 자체가 이 코드베이스에서는 거짓이다 — `services/tool_server/main.py`
# 의 도구 함수가 `-> dict` 로 표기돼 있으면 mcp 2.2.0 은 출력 스키마를 내지
# 않고, 출력 스키마가 없으면 서버는 구조화 콘텐츠가 아니라 `TextContent` 로
# 직렬화한다. 그래서 이 왕복을 실제 `MCPServer`/`Client` 로 도는 테스트가
# 근거를 지닌다 — 아래 두 테스트가 이 파일에서 유일하게 신뢰할 증거다.

from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer


def _real_run_tests(requirement_id: str) -> dict[str, Any]:
    """`services/tool_server/main.py::run_tests` 와 같은 표기 —
    `dict[str, Any]` 라야 mcp 2.2.0 이 출력 스키마를 내고, 그래야
    `structured_content` 가 채워진다."""
    return {"ok": False, "detail": "1 failed", "exit_code": 1}


def _real_write_file(requirement_id: str, path: str) -> dict[str, Any]:
    return {"ok": False, "detail": "워크스페이스를 벗어나는 경로다"}


async def test_real_mcp_round_trip_preserves_exit_code() -> None:
    """검증자 도구의 종료코드가 진짜 MCP 왕복을 거쳐도 살아남는다."""
    server = MCPServer("test-tools")
    server.add_tool(_real_run_tests, name="run_tests")
    async with Client(server) as client:
        bridge = ToolBridge(client.session, "REQ-7", ["run_tests"])
        out = await bridge.call("run_tests", {})
    assert out == {"ok": False, "detail": "1 failed", "exit_code": 1}


async def test_real_mcp_round_trip_tool_level_failure_is_not_inverted() -> None:
    """도구가 실행에는 성공했지만 업무적으로 거부한 경우(`ok: False`,
    `exit_code` 없음)가 `is_error`(MCP 프로토콜 성공/실패)와 뒤집히면
    안 된다."""
    server = MCPServer("test-tools")
    server.add_tool(_real_write_file, name="write_file")
    async with Client(server) as client:
        bridge = ToolBridge(client.session, "REQ-7", ["write_file"])
        out = await bridge.call("write_file", {"path": "../x"})
    assert out["ok"] is False


async def test_real_mcp_round_trip_crashing_tool_falls_back_to_is_error() -> None:
    """도구 함수 자체가 예외를 던지면(진짜 크래시) `is_error=True` 로
    남고, 그 경로도 여전히 `ok: False` 로 바뀐다."""

    def _crashy(requirement_id: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    server = MCPServer("test-tools")
    server.add_tool(_crashy, name="run_tests")
    async with Client(server) as client:
        bridge = ToolBridge(client.session, "REQ-7", ["run_tests"])
        out = await bridge.call("run_tests", {})
    assert out["ok"] is False
