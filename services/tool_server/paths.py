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
    base_resolved = base.resolve()
    root = (base_resolved / requirement_id).resolve()
    # requirement_id 는 "/" 를 포함할 수 없으므로(정규식이 이미 막는다), 벗어나지
    # 않는 한 root 는 항상 base 의 "직계 자식"이어야 한다. "." 은 root 를 base
    # 자체로 붕괴시키고(부모가 base 가 아니게 됨), ".." 은 base 의 부모로
    # 튀어오른다 — 둘 다 이 구조적 검사 하나로 막힌다. 문자열 블랙리스트가
    # 아니라 "직계 자식인가"를 보는 이유는, "."/".." 말고도 base 로 수렴하는
    # 표현이 나중에 생겨도 구조적으로 걸러지게 하기 위해서다.
    if root.parent != base_resolved or not _is_within(base_resolved, root):
        raise PathEscape(f"요구사항 루트가 base 를 벗어난다: {requirement_id!r}")
    _reject_case_collision(base_resolved, requirement_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _reject_case_collision(base: Path, requirement_id: str) -> None:
    """대소문자만 다른 형제 디렉터리와 충돌하는지 본다.

    macOS 의 기본 파일시스템(APFS)은 대소문자를 구분하지 않는다(대소문자는
    보존하지만). 계획 LLM 은 턴마다 대소문자를 다르게 쓸 수 있으므로
    `REQ-1`/`Req-1`/`req-1` 이 같은 디렉터리를 가리키며 서로의 파일을 덮어쓸 수
    있다. 소문자로 정규화하면 서로 다른 요구사항(별개의 기본키)이 워크스페이스
    하나를 공유하게 되어 같은 격리 실패를 다른 모양으로 재현하므로, 정규화 대신
    충돌을 거부한다.
    """
    if not base.is_dir():
        return
    for sibling in base.iterdir():
        if sibling.name == requirement_id:
            continue
        if sibling.name.lower() == requirement_id.lower():
            raise PathEscape(
                "요구사항 ID 가 기존 디렉터리와 대소문자만 다르게 충돌한다: "
                f"{requirement_id!r} vs {sibling.name!r}"
            )


def resolve_within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not _is_within(root.resolve(), candidate):
        raise PathEscape(f"워크스페이스를 벗어나는 경로다: {relative!r}")
    return candidate


def _is_within(root: Path, candidate: Path) -> bool:
    return root == candidate or root in candidate.parents
