from pathlib import Path

from tool_server.tools import (
    OUTPUT_TAIL_BYTES,
    READ_LIMIT_BYTES,
    list_files,
    read_file,
    run_security_scan,
    run_tests,
    write_file,
)


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
