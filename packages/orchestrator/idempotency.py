from __future__ import annotations
import hashlib
from collections.abc import Sequence


def idempotency_key(requirement_id: str, agent: str, revision: int,
                    input_hashes: Sequence[str]) -> str:
    """같은 (요구사항, 에이전트, 환류 회차, 입력)이면 같은 키.

    입력 Artifact 순서는 의미가 없으므로 정렬해 안정화한다.
    revision이 포함되므로 환류로 인한 정당한 재실행은 다른 키가 된다.
    """
    material = "|".join([requirement_id, agent, str(revision), *sorted(input_hashes)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
