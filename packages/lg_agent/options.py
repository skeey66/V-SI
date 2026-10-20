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
