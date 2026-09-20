import pytest

from tool_server.main import TOOLS_BY_ROLE, allowed_tools, root_for_request


def test_each_role_has_the_tools_the_spec_assigns() -> None:
    """스펙 §5.2 의 권한 표를 그대로 고정한다."""
    assert TOOLS_BY_ROLE["planner"] == ("write_file",)
    assert TOOLS_BY_ROLE["dev"] == ("list_files", "read_file", "write_file")
    assert TOOLS_BY_ROLE["qa"] == ("list_files", "read_file", "run_tests")
    assert TOOLS_BY_ROLE["security"] == ("list_files", "read_file", "run_security_scan")


def test_dev_cannot_run_tests() -> None:
    """개발에게 검증 권한을 주지 않는다 (스펙 §5.4)."""
    assert "run_tests" not in allowed_tools("dev")


def test_qa_cannot_write_files() -> None:
    assert "write_file" not in allowed_tools("qa")


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
