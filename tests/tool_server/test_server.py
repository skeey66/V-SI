import pytest

from tool_server.main import ROLE_SERVERS, TOOLS_BY_ROLE, allowed_tools, root_for_request


def test_each_role_has_the_tools_the_spec_assigns() -> None:
    """스펙 §5.2 의 권한 표를 그대로 고정한다."""
    assert TOOLS_BY_ROLE["planner"] == ("write_file",)
    assert TOOLS_BY_ROLE["dev"] == ("list_files", "read_file", "write_file")
    assert TOOLS_BY_ROLE["qa"] == ("list_files", "read_file", "run_tests")
    assert TOOLS_BY_ROLE["security"] == ("list_files", "read_file", "run_security_scan")


async def _registered_names(role: str) -> set[str]:
    return {t.name for t in await ROLE_SERVERS[role].list_tools()}


async def test_each_role_server_registers_exactly_its_allowed_tools() -> None:
    """`TOOLS_BY_ROLE` 이 죽은 선언이 아니라 실제 등록을 이끈다는 것을 검증한다.

    선언(`TOOLS_BY_ROLE`)이 아니라 각 역할의 `MCPServer` 인스턴스에 실제로
    등록된 도구 이름을 본다 — 데코레이터 오타나 등록 누락은 여기서 드러난다.
    """
    for role, names in TOOLS_BY_ROLE.items():
        assert await _registered_names(role) == set(names)


async def test_dev_cannot_run_tests() -> None:
    """개발에게 검증 권한을 주지 않는다 (스펙 §5.4).

    표가 아니라 dev 역할 서버에 실제로 등록된 도구를 본다 — `run_tests` 는
    dev 서버에 도구 자체가 존재하지 않아야 한다(권한 거부가 아니라 부재).
    """
    assert "run_tests" not in await _registered_names("dev")


async def test_qa_cannot_write_files() -> None:
    assert "write_file" not in await _registered_names("qa")


async def test_write_file_schema_carries_path_and_content() -> None:
    """Task 5 가 이 스키마로 클라이언트를 만든다 — 배선이 끊기면 여기서 잡는다."""
    tools = await ROLE_SERVERS["dev"].list_tools()
    write_file_tool = next(t for t in tools if t.name == "write_file")
    assert write_file_tool.input_schema["properties"].keys() >= {"path", "content"}


def test_unknown_role_gets_no_tools() -> None:
    assert allowed_tools("wat") == ()


def test_root_is_derived_from_header_not_from_tool_arguments(tmp_path) -> None:
    """모델은 루트를 지정할 수 없다 (스펙 §6.1)."""
    root = root_for_request(tmp_path, "REQ-1")
    assert root == (tmp_path / "REQ-1").resolve()


def test_root_rejects_hostile_requirement_id(tmp_path) -> None:
    from tool_server.paths import PathEscape

    with pytest.raises(PathEscape):
        root_for_request(tmp_path, "../../etc")
