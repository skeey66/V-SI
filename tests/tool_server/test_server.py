import pytest

from tool_server.main import ROLE_SERVERS, TOOLS_BY_ROLE, allowed_tools, root_for_request


def test_each_role_has_the_tools_the_spec_assigns() -> None:
    """스펙 §5.2 의 권한 표를 그대로 고정한다."""
    assert TOOLS_BY_ROLE["planner"] == ("write_file", "check_acceptance_tests")
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


async def test_every_registered_tool_has_an_output_schema() -> None:
    """`llm_agent/mcp_client.py::_call_result_to_dict` 는 mcp 가 `structured_content`
    를 채워준다는 데 기대는데, mcp 2.2.0 은 도구 함수의 반환 타입 표기가 맨
    `-> dict` 면(제네릭 인자 없이) 출력 스키마를 내지 않고, 출력 스키마가 없으면
    `structured_content` 가 항상 `None` 으로 온다(직접 왕복시켜 확인했다) —
    `exit_code` 가 텍스트 속에 묻혀 사라지는 바로 그 회귀다.

    아래 다섯 함수는 전부 `-> dict[str, Any]` 로 표기돼 있어야 한다. 이 테스트는
    그 표기를 직접 보는 대신(타입 표기는 런타임에 안 남는다) 실제로 등록된
    도구의 출력 스키마가 존재하는지 본다 — 표기가 맨 `dict` 로 되돌아가면 여기서
    바로 잡힌다. `llm_agent` 쪽의 `json.loads` 방어선(있다)이 이 회귀를 가려버려
    그 테스트 스위트만 봐서는 드러나지 않으므로, 이 표기의 계약은 이 파일이
    직접 지켜야 한다.
    """
    for role, names in TOOLS_BY_ROLE.items():
        tools = await ROLE_SERVERS[role].list_tools()
        by_name = {t.name: t for t in tools}
        for name in names:
            assert by_name[name].output_schema is not None, (
                f"{role}/{name} 의 도구 함수가 출력 스키마를 내지 않는다 — "
                "반환 타입 표기가 맨 `-> dict` 로 되돌아갔을 수 있다"
            )


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


async def test_only_planner_can_check_acceptance_tests() -> None:
    """확인 항목 점검은 기획 전용이다.

    개발이 이 도구를 가지면 자기가 통과시켜야 할 목표를 미리 돌려보며 맞춰
    갈 수 있고, 검증자가 가지면 판정 근거가 둘로 갈린다. 기획만 갖는다.
    """
    assert "check_acceptance_tests" in await _registered_names("planner")
    for role in ("dev", "qa", "security"):
        assert "check_acceptance_tests" not in await _registered_names(role)


async def test_dev_write_file_refuses_acceptance_test_paths(tmp_path) -> None:
    """개발의 `write_file` 만 인수 테스트 경로를 거부한다.

    같은 이름의 도구지만 역할마다 다른 구현이 등록된다(`_ROLE_TOOL_OVERRIDES`).
    선언이 아니라 **실제로 등록된 함수**를 불러서 확인한다 — 오버라이드 표에만
    적고 등록부에 반영하지 않는 실수는 여기서만 드러난다.

    실측 근거(REQ-SITE-101435): 개발이 1회차에 기획이 쓴 `test_booking.py` 를
    통째로 덮어썼고, QA 는 그 덮어쓴 테스트로 판정했다.
    """
    from tool_server import tools

    root = tmp_path / "ws"
    root.mkdir()

    blocked = tools.write_file(root, "test_booking.py", "def test_x(): pass", forbid_tests=True)
    assert not blocked.ok
    assert "테스트" in blocked.detail
    assert not (root / "test_booking.py").exists()

    # 구현 파일은 그대로 쓸 수 있어야 한다.
    allowed = tools.write_file(root, "booking.py", "x = 1", forbid_tests=True)
    assert allowed.ok
    assert (root / "booking.py").read_text(encoding="utf-8") == "x = 1"

    # 기획은 같은 경로에 쓸 수 있어야 한다 — 인수 테스트를 쓰는 것이 그 역할이다.
    planner = tools.write_file(root, "test_booking.py", "def test_x(): pass")
    assert planner.ok
    assert (root / "test_booking.py").exists()


async def test_dev_cannot_escape_the_test_path_guard_with_dot_segments(tmp_path) -> None:
    """`./sub/../test_x.py` 같은 우회가 통하면 안 된다.

    판별을 **정규화된 상대 경로**에 대고 하기 때문에 통하지 않는다 — 문자열
    검사로 막으려 했다면 통했을 모양이다.
    """
    from tool_server import tools

    root = tmp_path / "ws2"
    root.mkdir()
    r = tools.write_file(root, "sub/../test_sneaky.py", "x", forbid_tests=True)
    assert not r.ok
    assert not (root / "test_sneaky.py").exists()
