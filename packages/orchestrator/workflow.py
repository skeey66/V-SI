from __future__ import annotations
from enum import StrEnum


class RequirementState(StrEnum):
    PLANNED = "planned"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REMEDIATING = "remediating"
    BLOCKED = "blocked"
    ACCEPTED = "accepted"
    ESCALATED = "escalated"


class WorkflowSignal(StrEnum):
    PLAN_READY = "plan_ready"
    DEV_DONE = "dev_done"
    VERDICTS_PASS = "verdicts_pass"
    VERDICTS_FAIL = "verdicts_fail"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_GRANTED = "approval_granted"
    LIMIT_EXCEEDED = "limit_exceeded"


class IllegalTransition(RuntimeError):
    """현재 상태에서 허용되지 않는 신호."""


S, Sig = RequirementState, WorkflowSignal

TERMINAL: frozenset[RequirementState] = frozenset({S.ACCEPTED, S.ESCALATED})

_TABLE: dict[tuple[RequirementState, WorkflowSignal], RequirementState] = {
    (S.PLANNED, Sig.PLAN_READY): S.IMPLEMENTING,
    (S.IMPLEMENTING, Sig.DEV_DONE): S.VERIFYING,
    (S.IMPLEMENTING, Sig.APPROVAL_REQUIRED): S.BLOCKED,
    # 기획 게이트: 기획이 쓴 인수 테스트가 아무것도 검사하지 않으면 개발을
    # 보내지 않고 사람을 기다린다. `BLOCKED` 는 종료 상태가 아니고 리컨실러의
    # `ACTIVE` 집합에도 없다 — 푸는 것은 사람이다. 승인이 오면 기존
    # `(BLOCKED, APPROVAL_GRANTED) -> IMPLEMENTING` 을 그대로 타고 개발이
    # 디스패치된다.
    (S.PLANNED, Sig.APPROVAL_REQUIRED): S.BLOCKED,
    (S.BLOCKED, Sig.APPROVAL_GRANTED): S.IMPLEMENTING,
    (S.VERIFYING, Sig.VERDICTS_PASS): S.ACCEPTED,
    (S.VERIFYING, Sig.VERDICTS_FAIL): S.REMEDIATING,
    (S.REMEDIATING, Sig.DEV_DONE): S.IMPLEMENTING,
    (S.REMEDIATING, Sig.LIMIT_EXCEEDED): S.ESCALATED,
    # Task 12: 재시도 상한(캡)을 넘긴 에이전트는 반드시 REMEDIATING에서만
    # 나오지 않는다 — planner/dev가 영원히 크래시하면 PLANNED/IMPLEMENTING에서,
    # 검증 에이전트가 영원히 크래시하면 VERIFYING에서 리컨실러가 포기를
    # 결정한다(`engine.give_up`). 같은 신호를 재사용한다: "몇 번을 시도해도
    # 끝나지 않는다"는 의미가 같기 때문이다.
    (S.PLANNED, Sig.LIMIT_EXCEEDED): S.ESCALATED,
    (S.IMPLEMENTING, Sig.LIMIT_EXCEEDED): S.ESCALATED,
    (S.VERIFYING, Sig.LIMIT_EXCEEDED): S.ESCALATED,
}


def next_state(current: RequirementState, signal: WorkflowSignal) -> RequirementState:
    try:
        return _TABLE[(current, signal)]
    except KeyError:
        raise IllegalTransition(f"{current.name} 상태에서 {signal.name} 신호는 허용되지 않는다") from None
