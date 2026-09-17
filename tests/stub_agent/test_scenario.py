import pytest
from stub_agent.scenario import AgentScenario, DuplicateCallError

PATH = "scenarios/qa_fails_twice.yaml"


def test_verdict_sequence_is_consumed_in_order():
    s = AgentScenario.from_yaml(PATH, "qa")
    assert [s.next_verdict() for _ in range(3)] == ["FAIL", "FAIL", "PASS"]


def test_verdict_sequence_repeats_last_when_exhausted():
    s = AgentScenario.from_yaml(PATH, "qa")
    drained = [s.next_verdict() for _ in range(3)]
    assert drained == ["FAIL", "FAIL", "PASS"]
    # 소진 이후에는 마지막 값을 계속 반복해야 한다 — Task 11 의 단일 원소 시나리오가 이에 의존한다
    assert [s.next_verdict() for _ in range(5)] == ["PASS"] * 5


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


def test_single_verdict_repeats_indefinitely(tmp_path):
    import yaml
    p = tmp_path / "single.yaml"
    p.write_text(yaml.safe_dump({"scenario": "single", "agents": {"qa": {"verdicts": ["FAIL"]}}}),
                 encoding="utf-8")
    s = AgentScenario.from_yaml(str(p), "qa")
    assert [s.next_verdict() for _ in range(6)] == ["FAIL"] * 6
