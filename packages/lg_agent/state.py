"""그래프 상태.

기존 `orchestrator/workflow.py` 의 `RequirementState` 를 그대로 `status` 값으로
쓴다 — 두 구현의 결과를 같은 이름으로 비교하기 위해서다.
"""
from __future__ import annotations

from typing import Annotated, TypedDict


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """병렬 노드(qa·security)가 같은 키(`verdicts`)에 동시에 쓰기 위한 리듀서."""
    return {**(left or {}), **(right or {})}


class Verdict(TypedDict):
    agent: str
    verdict: str  # "PASS" | "FAIL" — 도구 종료코드에서만 온다
    exit_code: int | None
    summary: str


class VsiState(TypedDict, total=False):
    requirement_id: str
    title: str
    revision: int
    max_revisions: int
    status: str
    #: 기획 게이트(`check_acceptance_tests`) 결과
    gate: dict
    #: 이번 회차 검증 결과. 키는 에이전트 이름.
    verdicts: Annotated[dict[str, Verdict], merge_dicts]
    #: 개발에게 넘길 반려 사유 (`llm_agent.roles.task_prompt` 형식)
    feedback: list[dict]
    #: 회차별 기록 — 결과물 화면과 Before/After 비교에 쓴다
    history: Annotated[list[dict], lambda a, b: (a or []) + (b or [])]
