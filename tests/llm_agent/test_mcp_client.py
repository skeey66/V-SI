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
