"""실행 중에 관리자 화면에서 바꿀 수 있는 설정.

`Deps` 는 불변이지만 이 객체는 같은 인스턴스를 공유하며 값만 바뀐다. 다음에 시작하는
요구사항부터 적용된다 (이미 도는 그래프의 상태는 바꾸지 않는다).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeOptions:
    max_revisions: int = 3
    rag_enabled: bool = True
    #: Before/After 측정용. "legacy" 면 기존 `llm_agent.roles` 의 기획 프롬프트와
    #: 도구(`check_acceptance_tests` 포함)를 그대로 쓴다.
    planner_variant: str = "v2"
    #: 이전 회차의 반려 사유를 개발에게 계속 보여줄지. 끄면 직전 회차 지적만 간다
    #: (기존 경로와 같은 동작). Before/After 측정용 토글.
    feedback_memory: bool = True
