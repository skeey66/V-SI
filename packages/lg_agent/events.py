"""그래프 실행 중 화면으로 흘릴 사용자 정의 이벤트.

노드와 도구 어댑터가 `emit()` 을 부르면 LangGraph 의 `custom` 스트림으로 나간다.
API 는 이것을 SSE 로 그대로 중계한다. 그래프 밖(단위 테스트 등)에서 부르면 writer 가
없으므로 조용히 넘어간다.
"""
from __future__ import annotations

from langgraph.config import get_stream_writer


def emit(event: dict) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer(event)
