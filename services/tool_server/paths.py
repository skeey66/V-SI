"""워크스페이스 경로 봉쇄.

SP2 의 유일한 보안 경계다. LLM 은 워크스페이스 루트를 모르고 상대경로만 쓴다
(스펙 §6.1). 여기서 막지 못하면 생성 코드가 호스트 파일시스템에 닿는다.

문자열 검사로는 부족하다 — 심볼릭 링크는 해석해야 드러난다. 그래서 모든 경로를
`resolve()` 한 뒤 루트 하위인지 본다.
"""
from __future__ import annotations

import re
from pathlib import Path


class PathEscape(ValueError):
    """워크스페이스 루트를 벗어나는 경로."""


#: 요구사항 ID 에 허용하는 문자. SP2 에서는 기획 LLM 이 ID 를 만들 수 있으므로
#: 디렉터리 이름으로 쓰기 전에 좁힌다.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def workspace_root(base: Path, requirement_id: str) -> Path:
    if not _SAFE_ID.match(requirement_id):
        raise PathEscape(f"요구사항 ID 에 쓸 수 없는 문자가 있다: {requirement_id!r}")
    root = (base / requirement_id).resolve()
    if not _is_within(base.resolve(), root):
        raise PathEscape(f"요구사항 루트가 base 를 벗어난다: {requirement_id!r}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not _is_within(root.resolve(), candidate):
        raise PathEscape(f"워크스페이스를 벗어나는 경로다: {relative!r}")
    return candidate


def _is_within(root: Path, candidate: Path) -> bool:
    return root == candidate or root in candidate.parents
