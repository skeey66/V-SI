from pathlib import Path

import pytest

from tool_server.paths import PathEscape, resolve_within, workspace_root


def test_normal_relative_path_resolves_under_root(tmp_path: Path) -> None:
    root = workspace_root(tmp_path, "REQ-1")
    assert resolve_within(root, "calc.py") == root / "calc.py"
    assert resolve_within(root, "pkg/mod.py") == root / "pkg" / "mod.py"


def test_parent_escape_is_rejected(tmp_path: Path) -> None:
    root = workspace_root(tmp_path, "REQ-1")
    with pytest.raises(PathEscape):
        resolve_within(root, "../REQ-2/secret.py")


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    root = workspace_root(tmp_path, "REQ-1")
    with pytest.raises(PathEscape):
        resolve_within(root, "/etc/passwd")


def test_symlink_pointing_outside_is_rejected(tmp_path: Path) -> None:
    """심볼릭 링크는 문자열 검사로 못 잡는다 — 해석 후에 검사해야 한다."""
    root = workspace_root(tmp_path, "REQ-1")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    with pytest.raises(PathEscape):
        resolve_within(root, "link.txt")


def test_requirement_ids_get_separate_roots(tmp_path: Path) -> None:
    a = workspace_root(tmp_path, "REQ-1")
    b = workspace_root(tmp_path, "REQ-2")
    assert a != b
    assert a.is_dir() and b.is_dir()


def test_requirement_id_cannot_escape_base(tmp_path: Path) -> None:
    """요구사항 ID 는 SP2 에서 기획 LLM 이 만들 수 있다 — 루트 이름도 봉쇄한다."""
    with pytest.raises(PathEscape):
        workspace_root(tmp_path, "../elsewhere")
