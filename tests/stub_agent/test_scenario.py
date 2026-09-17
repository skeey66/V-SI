import pytest
import yaml

from stub_agent.scenario import AgentScenario, ScenarioError

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


def test_single_verdict_repeats_indefinitely(tmp_path):
    import yaml
    p = tmp_path / "single.yaml"
    p.write_text(yaml.safe_dump({"scenario": "single", "agents": {"qa": {"verdicts": ["FAIL"]}}}),
                 encoding="utf-8")
    s = AgentScenario.from_yaml(str(p), "qa")
    assert [s.next_verdict() for _ in range(6)] == ["FAIL"] * 6


# --------------------------------------------------- 오타는 크게 터져야 한다


def _write(tmp_path, spec: dict) -> str:
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"scenario": "t", "agents": {"dev": spec}}),
                 encoding="utf-8")
    return str(p)


def test_unknown_injection_mode_raises(tmp_path):
    """`attempt_2: hang`은 파싱은 되고 아무 일도 안 하는 대신 터져야 한다.

    executor는 주입 모드를 `"crash"`와만 비교한다. 그래서 오타 난 모드는
    조용히 무시됐고, 시나리오는 **실패를 주입하지 않은 채** 정상 통과했다 —
    실패 주입 테스트가 아무것도 시험하지 않으면서 초록이 되는 가장 나쁜
    종류의 침묵이다.
    """
    with pytest.raises(ScenarioError, match="hang"):
        AgentScenario.from_yaml(_write(tmp_path, {"attempt_2": "hang"}), "dev")


def test_misspelled_attempt_key_raises(tmp_path):
    """`attempt2`(밑줄 누락)는 접두사에 안 걸려 통째로 무시됐다."""
    with pytest.raises(ScenarioError, match="attempt2"):
        AgentScenario.from_yaml(_write(tmp_path, {"attempt2": "crash"}), "dev")


def test_unknown_spec_key_raises(tmp_path):
    with pytest.raises(ScenarioError, match="latency_msec"):
        AgentScenario.from_yaml(_write(tmp_path, {"latency_msec": 10}), "dev")


def test_known_keys_still_parse(tmp_path):
    s = AgentScenario.from_yaml(
        _write(tmp_path, {"verdicts": ["PASS"], "latency_ms": 5, "attempt_3": "crash"}),
        "dev",
    )
    assert s.verdicts == ["PASS"] and s.latency_ms == 5
    assert s.failure_for_attempt(3) == "crash"


def test_every_shipped_scenario_parses_for_every_agent():
    """리포에 든 시나리오 파일 전부가 새 검증을 통과한다(회귀 방지)."""
    import pathlib as _pl
    for path in sorted(_pl.Path("scenarios").glob("*.yaml")):
        for agent in ("planner", "dev", "qa", "security"):
            AgentScenario.from_yaml(str(path), agent)
