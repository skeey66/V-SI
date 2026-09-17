import pytest
from orchestrator.workflow import (
    RequirementState as S, WorkflowSignal as Sig,
    next_state, IllegalTransition, TERMINAL,
)


def test_happy_path_reaches_accepted():
    s = S.PLANNED
    s = next_state(s, Sig.PLAN_READY)
    assert s is S.IMPLEMENTING
    s = next_state(s, Sig.DEV_DONE)
    assert s is S.VERIFYING
    s = next_state(s, Sig.VERDICTS_PASS)
    assert s is S.ACCEPTED


def test_failed_verdict_loops_back_to_implementing():
    s = next_state(S.VERIFYING, Sig.VERDICTS_FAIL)
    assert s is S.REMEDIATING
    s = next_state(s, Sig.DEV_DONE)
    assert s is S.IMPLEMENTING


def test_limit_exceeded_escalates():
    assert next_state(S.REMEDIATING, Sig.LIMIT_EXCEEDED) is S.ESCALATED


def test_approval_gate_round_trip():
    s = next_state(S.IMPLEMENTING, Sig.APPROVAL_REQUIRED)
    assert s is S.BLOCKED
    assert next_state(s, Sig.APPROVAL_GRANTED) is S.IMPLEMENTING


def test_terminal_states_reject_all_signals():
    for terminal in TERMINAL:
        with pytest.raises(IllegalTransition):
            next_state(terminal, Sig.DEV_DONE)


def test_unknown_transition_raises():
    with pytest.raises(IllegalTransition, match="PLANNED"):
        next_state(S.PLANNED, Sig.VERDICTS_PASS)
