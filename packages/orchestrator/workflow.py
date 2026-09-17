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
    (S.BLOCKED, Sig.APPROVAL_GRANTED): S.IMPLEMENTING,
    (S.VERIFYING, Sig.VERDICTS_PASS): S.ACCEPTED,
    (S.VERIFYING, Sig.VERDICTS_FAIL): S.REMEDIATING,
    (S.REMEDIATING, Sig.DEV_DONE): S.IMPLEMENTING,
    (S.REMEDIATING, Sig.LIMIT_EXCEEDED): S.ESCALATED,
}


def next_state(current: RequirementState, signal: WorkflowSignal) -> RequirementState:
    try:
        return _TABLE[(current, signal)]
    except KeyError:
        raise IllegalTransition(f"{current.name} 상태에서 {signal.name} 신호는 허용되지 않는다") from None
