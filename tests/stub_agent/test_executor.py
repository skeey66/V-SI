import pytest
from stub_agent.executor import StubExecutor, ARTIFACT_KIND, StubCrash
from stub_agent.scenario import AgentScenario

PATH = "scenarios/qa_fails_twice.yaml"


def test_artifact_kind_per_agent():
    assert ARTIFACT_KIND["planner"] == "requirements"
    assert ARTIFACT_KIND["dev"] == "source_code"
    assert ARTIFACT_KIND["qa"] == "test_report"
    assert ARTIFACT_KIND["security"] == "security_report"


def test_qa_payload_carries_exit_code_not_claim():
    ex = StubExecutor(AgentScenario.from_yaml(PATH, "qa"), "qa")
    payload = ex.build_payload(attempt=1)
    assert payload["verdict"] == "FAIL"
    assert payload["exit_code"] != 0


def test_passing_verdict_has_zero_exit_code():
    s = AgentScenario.from_yaml(PATH, "qa")
    ex = StubExecutor(s, "qa")
    ex.build_payload(attempt=1)
    ex.build_payload(attempt=2)
    payload = ex.build_payload(attempt=3)
    assert payload["verdict"] == "PASS" and payload["exit_code"] == 0


def test_injected_crash_raises():
    ex = StubExecutor(AgentScenario.from_yaml(PATH, "dev"), "dev")
    with pytest.raises(StubCrash):
        ex.build_payload(attempt=2)
