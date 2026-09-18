from __future__ import annotations
import hashlib
from collections.abc import Sequence


def idempotency_key(requirement_id: str, agent: str, revision: int,
                    input_hashes: Sequence[str], attempt: int = 1) -> str:
    """같은 (요구사항, 에이전트, 환류 회차, 입력, 시도)면 같은 키.

    **이 키가 실제로 하는 일은 둘이다**(스펙 §8):

    1. `workflow_tasks.idempotency_key`의 유니크 태그 — 같은 (요구사항,
       에이전트, 회차, 시도)에 행이 둘 생기는 것을 DB가 막는다. 동시
       디스패처는 둘 다 같은 `attempt`를 계산해 같은 키를 만들고, 뒤늦은
       쪽이 유니크 위반으로 떨어진다.
    2. 계보 — `revision`이 있으므로 환류로 인한 정당한 재실행은 다른 키가
       되고, `attempt`가 있으므로 크래시한 행을 대체하는 재디스패치도 다른
       키가 된다.

    **에이전트는 이 키로 중복을 제거하지 않는다.** 그럴 필요가 없기 때문이다 —
    제자리 재시도가 `retry.is_undelivered`(증명된 미전달)로 좁혀진 뒤로는 같은
    키가 에이전트에 두 번 도달하는 경로가 없다. 그 전제가 깨지면(제자리 재시도
    범위 확대) 에이전트측 멱등성이 **필수**가 된다 — 스펙 §12.1의 SP2 경고.

    `attempt`는 **크래시 복구(Task 13)** 때문에 들어왔다. 첫 시도(attempt=1)의
    키는 이 인자가 생기기 전과 **같은 값**이다 — 재료에 아예 넣지 않는다.

    `input_hashes`는 확장 지점이고 **현재 운영 경로는 쓰지 않는다**: 유일한
    호출부(`engine.dispatch_agent`)가 빈 시퀀스를 넘기므로 재료에 아무것도
    보태지 않는다. 그래서 스펙의 키 재료에서도 뺐다 — 코드가 공급하지 않는
    재료를 스펙이 묘사하면 안 된다. 입력 Artifact로 키를 가르는 것이 필요해지면
    (SP2) 정렬·길이 접두사 처리는 여기 이미 있다.

    필드 경계는 길이 접두사로 인코딩되어 구분자 주입을 방지한다.
    """
    def encode_field(field: str) -> str:
        return f"{len(field)}:{field}"

    fields = [requirement_id, agent, str(revision), *sorted(input_hashes)]
    if attempt > 1:
        fields.append(f"attempt={attempt}")
    material = "|".join(encode_field(f) for f in fields)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
