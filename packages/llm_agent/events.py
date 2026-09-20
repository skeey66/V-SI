"""에이전트가 쓰는 자문 이벤트.

**이 이벤트에는 워크플로 권위가 없다** (스펙 §8.1). UI 리듀서도 리컨실러도
여기서 상태를 유도해서는 안 된다. 상태의 근거는 언제나 오케스트레이터가 상태
전이와 한 트랜잭션에 쓴 `state_changed` 다.

이 규칙을 깨면 트랜잭셔널 아웃박스의 보장이 무너진다 — 에이전트의 이벤트 쓰기는
워크플로 전이와 원자적이지 않으므로, 이벤트는 있는데 상태는 안 바뀐 창이 항상
존재한다. 그래서 payload 에 `state`·`verdict` 같은, 상태를 유도하고 싶게
만드는 필드를 싣지 않는다.

`result` 는 이미 소독된 값이다 — `detail` 문자열에서 워크스페이스 절대경로를
지우는 일도, MCP 결과 객체를 평범한 dict 로 바꾸는 일도 이 함수보다 앞단(도구
계층·MCP 브리지)의 책임이다. 여기서는 그 위에 길이만 자른다.

**동기 호출 지점에서 어떻게 부를지(인라인 await vs 백그라운드 디스패치)**: 이
모듈은 `async def` 로만 제공한다 — 스스로 백그라운드로 던지지 않는다. 호출
지점(`run_loop` 의 `on_tool`)에서 매 도구 호출마다 인라인으로 `await` 하도록
권한다: 이 함수가 하는 일은 커밋 하나뿐이라 LLM 턴(12~15초)·도구 호출 상한
(최대 90초)에 비해 지연이 무시할 만한 수준이고, 인라인이면 쓰기 실패가
조용히 삼켜지지 않고 호출자에게 그대로 보인다. `asyncio.create_task` 로
백그라운드에 던지는 방식은 일부러 피했다 — 그러려면 태스크 객체를 어딘가
살아있는 컬렉션에 붙잡아 둬야 하는데(파이썬은 참조가 없는 태스크를 가비지
컬렉트할 수 있다), 이 코드베이스에서는 바로 그 fire-and-forget 참조 유실이
이미 리뷰에서 지적된 살아있는 위험이다. 게다가 에이전트 프로세스가 이 도구
호출을 마지막으로 끝나버리면, 백그라운드 태스크가 커밋을 마치기도 전에
프로세스와 함께 사라질 수 있다 — 그러면 "자문 이벤트가 UI에 안 보인다"는
현상이 원인 불명 상태로 남는다. 인라인 await 는 이 두 위험을 구조적으로
없앤다.
"""
from __future__ import annotations

from orchestrator.models import OutboxEvent

#: UI 표시용이므로 짧게 자른다. 전체 출력은 아티팩트에 남는다.
_DETAIL_LIMIT = 512


async def record_tool_event(
    session_maker,
    *,
    requirement_id: str,
    agent: str,
    revision: int,
    tool: str,
    result: dict,
) -> None:
    payload = {
        "agent": agent,
        "tool": tool,
        "revision": revision,
        "ok": bool(result.get("ok")),
        "detail": str(result.get("detail", ""))[:_DETAIL_LIMIT],
    }
    if result.get("exit_code") is not None:
        payload["exit_code"] = result["exit_code"]
    async with session_maker() as s:
        s.add(
            OutboxEvent(
                aggregate="requirement",
                aggregate_id=requirement_id,
                event_type="tool_result",
                payload=payload,
            )
        )
        await s.commit()
