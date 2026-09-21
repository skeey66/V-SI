import os
import subprocess
import sys
from pathlib import Path

from tool_server.tools import (
    check_acceptance_tests,
    OUTPUT_TAIL_BYTES,
    READ_LIMIT_BYTES,
    list_files,
    read_file,
    run_security_scan,
    run_tests,
    write_file,
)

_SERVICES_DIR = str(Path(__file__).resolve().parents[2] / "services")


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    assert write_file(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n").ok
    result = read_file(tmp_path, "calc.py")
    assert result.ok
    assert "def add" in result.detail


def test_path_escape_returns_error_result_not_exception(tmp_path: Path) -> None:
    """도구 오류는 예외가 아니라 결과다 — 모델이 보고 고쳐야 한다 (스펙 §6.1)."""
    result = write_file(tmp_path, "../escape.py", "x = 1")
    assert not result.ok
    assert "벗어나는" in result.detail


def test_list_files_returns_relative_paths(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "")
    write_file(tmp_path, "pkg/b.py", "")
    result = list_files(tmp_path)
    assert result.ok
    assert "a.py" in result.detail
    assert "pkg/b.py" in result.detail
    assert str(tmp_path) not in result.detail  # 루트를 노출하지 않는다


def test_read_file_truncates_at_limit(tmp_path: Path) -> None:
    write_file(tmp_path, "big.py", "x" * (READ_LIMIT_BYTES + 5_000))
    result = read_file(tmp_path, "big.py")
    assert result.ok
    assert len(result.detail.encode()) <= READ_LIMIT_BYTES + 200  # 절단 표시 여유
    assert "절단" in result.detail


def test_read_missing_file_is_error_result(tmp_path: Path) -> None:
    result = read_file(tmp_path, "nope.py")
    assert not result.ok


def test_run_tests_passes_on_green_suite(tmp_path: Path) -> None:
    write_file(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    write_file(tmp_path, "test_calc.py", "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = run_tests(tmp_path)
    assert result.exit_code == 0
    assert result.ok


def test_run_tests_fails_on_red_suite_and_keeps_tail(tmp_path: Path) -> None:
    write_file(tmp_path, "calc.py", "def add(a, b):\n    return 0\n")
    write_file(tmp_path, "test_calc.py", "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = run_tests(tmp_path)
    assert result.exit_code == 1
    assert not result.ok
    assert len(result.detail.encode()) <= OUTPUT_TAIL_BYTES * 2 + 200
    assert "test_add" in result.detail  # 실패한 테스트 이름이 꼬리에 남는다


def test_run_security_scan_flags_known_bad_pattern(tmp_path: Path) -> None:
    write_file(tmp_path, "bad.py", "import subprocess\nsubprocess.call('ls', shell=True)\n")
    result = run_security_scan(tmp_path)
    assert result.exit_code != 0
    assert not result.ok


def test_run_security_scan_passes_on_clean_code(tmp_path: Path) -> None:
    write_file(tmp_path, "good.py", "def add(a, b):\n    return a + b\n")
    result = run_security_scan(tmp_path)
    assert result.exit_code == 0
    assert result.ok


def test_write_file_parent_path_collides_with_existing_file_is_error_result(tmp_path: Path) -> None:
    """utils.py 를 나중에 utils/helpers.py 로 리팩터링하면 상위 경로가 파일과 충돌한다."""
    assert write_file(tmp_path, "foo", "x = 1").ok
    result = write_file(tmp_path, "foo/bar.py", "y = 2")
    assert not result.ok
    assert "파일" in result.detail and "디렉터리" in result.detail


def test_write_file_target_collides_with_existing_directory_is_error_result(tmp_path: Path) -> None:
    (tmp_path / "pkgdir").mkdir()
    result = write_file(tmp_path, "pkgdir", "x = 1")
    assert not result.ok


def test_run_tests_missing_root_is_error_result_not_exception(tmp_path: Path) -> None:
    missing_root = tmp_path / "does-not-exist"
    result = run_tests(missing_root)
    assert not result.ok


def test_read_file_permission_denied_is_error_result(tmp_path: Path) -> None:
    write_file(tmp_path, "secret.py", "x = 1")
    target = tmp_path / "secret.py"
    target.chmod(0o000)
    try:
        result = read_file(tmp_path, "secret.py")
    finally:
        target.chmod(0o644)
    assert not result.ok


def test_write_file_error_detail_does_not_leak_workspace_root(tmp_path: Path) -> None:
    """스펙: 모델은 워크스페이스 루트를 몰라야 한다 — list_files 와 같은 불변식."""
    assert write_file(tmp_path, "foo", "x = 1").ok
    result = write_file(tmp_path, "foo/bar.py", "y = 2")
    assert not result.ok
    assert str(tmp_path) not in result.detail
    assert str(tmp_path.resolve()) not in result.detail


def test_run_tests_missing_root_error_detail_does_not_leak_workspace_root(tmp_path: Path) -> None:
    missing_root = tmp_path / "does-not-exist"
    result = run_tests(missing_root)
    assert not result.ok
    assert str(missing_root) not in result.detail
    assert str(missing_root.resolve()) not in result.detail


def test_read_file_permission_denied_detail_does_not_leak_workspace_root(tmp_path: Path) -> None:
    write_file(tmp_path, "secret.py", "x = 1")
    target = tmp_path / "secret.py"
    target.chmod(0o000)
    try:
        result = read_file(tmp_path, "secret.py")
    finally:
        target.chmod(0o644)
    assert not result.ok
    assert str(tmp_path) not in result.detail
    assert str(tmp_path.resolve()) not in result.detail


def test_write_file_with_lone_surrogate_is_error_result_not_exception(tmp_path: Path) -> None:
    """Ollama 가 JSON 으로 돌려주는 도구 인자에 깨진 대리쌍이 섞일 수 있다."""
    result = write_file(tmp_path, "bad.py", "x = '\ud800'")
    assert not result.ok


def test_run_tests_collection_error_does_not_leak_workspace_root(tmp_path: Path) -> None:
    """pytest 의 assertion-rewrite 는 ast.parse(filename=<절대경로>) 를 쓰므로
    수집 오류 출력에 절대경로가 그대로 찍힌다 — cwd 를 워크스페이스로 잡아도 샌다."""
    write_file(tmp_path, "test_broken.py", "def test_x(\n")  # 문법 오류
    result = run_tests(tmp_path)
    assert not result.ok
    assert str(tmp_path) not in result.detail
    assert str(tmp_path.resolve()) not in result.detail


def test_strip_root_does_not_corrupt_output_when_root_is_substring_of_resolved(
) -> None:
    """macOS 에서 `/var` 는 `/private/var` 심볼릭 링크다. 그래서 base 가 그
    아래 있으면 `str(root)` 가 `str(root.resolve())` 안에 그대로 박혀 있는 게
    정상이다(round 3 의 두 후보 이유). `_strip_root` 가 `{str(root),
    str(root.resolve())}` 처럼 순서 없는 set 을 그대로 돌면, 어느 후보가
    먼저 치환되는지는 PYTHONHASHSEED 에 따라 프로세스마다 달라진다 — 짧은
    후보가 먼저 치환되면 긴 후보 속에 파묻힌 자신의 occurrence 를 갉아먹어
    "/private" 접두사 잔해가 뒤 내용에 들러붙는다
    (`File "/privatetest_calc.py"` 처럼).

    이 테스트는 한 번의 실행으로 버그를 재현할 확률이 반반이 되는 걸 피하려고,
    이 문자열 쌍에서 버그 순서를 내는 것으로 실측 확인한 PYTHONHASHSEED=1 을
    고정한 서브프로세스에서 `_strip_root` 를 직접 호출한다 — 실제 pytest 를
    또 띄우는 대신 이 함수 자체의 계약을 검증한다. 수정본(길이 내림차순 정렬)
    은 순서에 의존하지 않으므로 어느 시드에서도 항상 올바르게 나온다.
    """
    script = (
        "from pathlib import Path\n"
        "from tool_server.tools import _strip_root\n"
        "root = Path('/var/folders/xx/tmp-req-1')\n"
        "text = 'File \"' + str(root.resolve()) + '/test_calc.py\", line 1'\n"
        "print(_strip_root(root, text))\n"
    )
    env = dict(os.environ, PYTHONHASHSEED="1", PYTHONPATH=_SERVICES_DIR)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    result = proc.stdout.strip()
    assert "/private" not in result
    assert "/var/folders" not in result
    assert result == 'File "test_calc.py", line 1'


def test_security_scan_does_not_flag_asserts_in_acceptance_tests(tmp_path: Path) -> None:
    """기획이 쓴 인수 테스트의 `assert` 때문에 보안이 FAIL 하면 안 된다.

    bandit 의 `B101: assert_used` 는 테스트 파일까지 잡는다. 기획 에이전트가
    pytest 인수 테스트를 쓰는 것이 이 시스템의 설계이므로, 제외하지 않으면
    **설계대로 동작하는 실행이 영원히 보안 FAIL** 이 된다(실측: 실제 LLM
    실행 3회차 전부 이 사유로 반려됐다).
    """
    write_file(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    write_file(
        tmp_path,
        "test_calc.py",
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    result = run_security_scan(tmp_path)
    assert result.exit_code == 0, f"인수 테스트의 assert 가 잡혔다: {result.detail}"
    assert result.ok


def test_security_scan_still_flags_real_issues_in_implementation(tmp_path: Path) -> None:
    """테스트를 제외해도 구현 파일의 실제 문제는 계속 잡아야 한다."""
    write_file(tmp_path, "bad.py", "import subprocess\nsubprocess.call('ls', shell=True)\n")
    write_file(
        tmp_path,
        "test_bad.py",
        "def test_placeholder():\n    assert True\n",
    )
    result = run_security_scan(tmp_path)
    assert result.exit_code != 0
    assert not result.ok


# ─────────────────────────────────────────────────────────────────────────────
# check_acceptance_tests — 구현이 없는 방에서 인수 테스트를 돌려 "빨간가" 본다.
#
# 아래 두 시험은 실제 LLM 실행에서 나온 산출물을 그대로 옮긴 것이다. 지어낸
# 실패 모양이 아니라 **실제로 두 번 겪은 실패 모양**이다.
# ─────────────────────────────────────────────────────────────────────────────


def test_acceptance_tests_that_pass_without_an_implementation_are_rejected(
    tmp_path: Path,
) -> None:
    """REQ-SITE-101435 재현: 본문이 `assert True` 였다.

    구현이 하나도 없는데 4개가 전부 통과했다(pytest exit 0). 개발이 무엇을
    만들어도 통과하므로 목표가 되지 못한다. 이 도구는 그것을 **불합격**(1)로
    옮긴다 — pytest 의 0 이 여기서는 0 이 아니다.
    """
    write_file(
        tmp_path,
        "test_booking.py",
        "import pytest\n\n"
        "@pytest.mark.parametrize('room,expected', [(1, True), (2, False)])\n"
        "def test_book_room(room, expected):\n"
        "    # 실제 로직 검증 코드가 여기에 있어야 함\n"
        "    assert True\n",
    )
    result = check_acceptance_tests(tmp_path)
    assert result.exit_code == 1
    assert not result.ok
    assert "아무것도 검사하지 않는다" in result.detail


def test_acceptance_tests_that_collect_nothing_are_rejected(tmp_path: Path) -> None:
    """REQ-CART-095427 재현: 모듈 최상단 `assert` 만 썼다.

    pytest 는 `def test_...()` 만 테스트로 본다. 최상단 assert 는 import 시점에
    한 번 돌고 끝이라 수집 결과가 0개다(exit 5). 검사할 것이 없으므로 불합격.
    """
    write_file(
        tmp_path,
        "test_cart.py",
        "def total(items):\n    return sum(items)\n\n"
        "assert total([1, 2]) == 3\n",
    )
    result = check_acceptance_tests(tmp_path)
    assert result.exit_code == 1
    assert not result.ok
    assert "수집되지 않았다" in result.detail


def test_acceptance_tests_that_fail_for_the_right_reason_are_accepted(
    tmp_path: Path,
) -> None:
    """쓸 만한 인수 테스트는 구현이 없으면 **import 에서** 터진다(pytest exit 2).

    이것이 정상이다 — 부를 대상이 아직 없다는 뜻이고, 그래서 개발에게 줄
    목표가 된다.
    """
    write_file(
        tmp_path,
        "test_booking.py",
        "from booking import add_booking\n\n"
        "def test_conflict():\n"
        "    assert add_booking('A', '10:00', '11:00') is True\n",
    )
    result = check_acceptance_tests(tmp_path)
    assert result.exit_code == 0
    assert result.ok
    assert "제구실을 한다" in result.detail


def test_acceptance_tests_that_assert_a_wrong_expectation_are_accepted(
    tmp_path: Path,
) -> None:
    """구현이 **있는데** 틀린 경우도 합격이다(pytest exit 1).

    이 도구는 "테스트가 무언가를 실제로 단언하는가"만 본다. 기획 직후에는
    워크스페이스에 구현이 없지만, 기획이 스스로 더미를 써 두는 경우까지
    막을 이유는 없다 — 단언이 살아 있으면 목표로 쓸 수 있다.
    """
    write_file(tmp_path, "calc.py", "def add(a, b):\n    return 0\n")
    write_file(
        tmp_path,
        "test_calc.py",
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    result = check_acceptance_tests(tmp_path)
    assert result.exit_code == 0
    assert result.ok


def test_empty_workspace_is_rejected(tmp_path: Path) -> None:
    """기획이 `spec.md` 만 쓰고 테스트를 안 쓴 경우도 걸린다."""
    write_file(tmp_path, "spec.md", "# 명세\n두 수를 더한다.\n")
    result = check_acceptance_tests(tmp_path)
    assert result.exit_code == 1
    assert not result.ok
