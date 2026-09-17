import pytest
from stub_agent.scenario import AgentScenario, DuplicateCallError

PATH = "scenarios/qa_fails_twice.yaml"


def test_verdict_sequence_is_consumed_in_order():
    s = AgentScenario.from_yaml(PATH, "qa")
    assert [s.next_verdict() for _ in range(3)] == ["FAIL", "FAIL", "PASS"]


def test_verdict_sequence_repeats_last_when_exhausted():
    s = AgentScenario.from_yaml(PATH, "qa")
    for _ in range(3):
        s.next_verdict()
    assert s.next_verdict() == "PASS"


def test_failure_injected_on_specific_attempt():
    s = AgentScenario.from_yaml(PATH, "dev")
    assert s.failure_for_attempt(1) is None
    assert s.failure_for_attempt(2) == "crash"


def test_latency_is_read():
    assert AgentScenario.from_yaml(PATH, "dev").latency_ms == 200
    assert AgentScenario.from_yaml(PATH, "qa").latency_ms == 0


def test_duplicate_idempotency_key_is_detected():
    s = AgentScenario.from_yaml(PATH, "dev")
    assert s.record_call("key-1") == 1
    with pytest.raises(DuplicateCallError, match="key-1"):
        s.record_call("key-1")
