from __future__ import annotations
import hashlib
from collections.abc import Sequence


def idempotency_key(requirement_id: str, agent: str, revision: int,
                    input_hashes: Sequence[str], attempt: int = 1) -> str:
    """같은 (요구사항, 에이전트, 환류 회차, 입력, 시도)면 같은 키.

    입력 Artifact 순서는 의미가 없으므로 정렬해 안정화한다.
    revision이 포함되므로 환류로 인한 정당한 재실행은 다른 키가 된다.

    `attempt`는 **크래시 복구(Task 13)** 때문에 들어왔다. 같은 회차의 같은
    에이전트를 다시 보내야 하는데(에이전트 Task가 죽었다) Task 행은 불변이라
    새 행을 만들어야 하고, `workflow_tasks.idempotency_key`에는 유니크 제약이
    있으므로 회차만으로는 키가 충돌한다. 첫 시도(attempt=1)의 키는 이 인자가
    생기기 전과 **같은 값**이다 — 재료에 아예 넣지 않는다.

    필드 경계는 길이 접두사로 인코딩되어 구분자 주입을 방지한다.
    """
    def encode_field(field: str) -> str:
        return f"{len(field)}:{field}"

    fields = [requirement_id, agent, str(revision), *sorted(input_hashes)]
    if attempt > 1:
        fields.append(f"attempt={attempt}")
    material = "|".join(encode_field(f) for f in fields)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
