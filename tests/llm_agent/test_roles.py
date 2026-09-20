from llm_agent.roles import ROLES, system_prompt, task_prompt


def test_four_roles_exist_with_sp1_artifact_kinds() -> None:
    """SP1 의 `kind` 를 그대로 쓴다 — 오케스트레이터가 이 값으로 분기한다."""
    assert ROLES["planner"].artifact_kind == "requirements"
    assert ROLES["dev"].artifact_kind == "source_code"
    assert ROLES["qa"].artifact_kind == "test_report"
    assert ROLES["security"].artifact_kind == "security_report"


def test_only_verifiers_have_a_verdict_tool() -> None:
    assert ROLES["qa"].is_verifier and ROLES["qa"].verdict_tool == "run_tests"
    assert ROLES["security"].is_verifier and ROLES["security"].verdict_tool == "run_security_scan"
    assert not ROLES["dev"].is_verifier and ROLES["dev"].verdict_tool is None
    assert not ROLES["planner"].is_verifier and ROLES["planner"].verdict_tool is None


def test_planner_prompt_demands_acceptance_tests() -> None:
    """기획이 인수 테스트를 쓰는 것이 SP2 의 핵심 결정이다 (스펙 §7)."""
    prompt = system_prompt(ROLES["planner"])
    assert "test_" in prompt
    assert "pytest" in prompt


def test_verifier_prompt_says_it_does_not_decide_the_verdict() -> None:
    prompt = system_prompt(ROLES["qa"])
    assert "판정" in prompt


def test_first_revision_prompt_has_no_feedback_section() -> None:
    prompt = task_prompt("계산기", 1, [])
    assert "계산기" in prompt
    assert "반려" not in prompt


def test_later_revision_prompt_carries_failure_detail_verbatim() -> None:
    """완료 기준 4: 환류가 실제 실패 출력에 근거한다 (스펙 §13)."""
    feedback = [{"agent": "qa", "verdict": "FAIL",
                 "summary": "test_add_negative: assert add(-1,-1) == -2, 실제 0"}]
    prompt = task_prompt("계산기", 2, feedback)
    assert "test_add_negative" in prompt
    assert "실제 0" in prompt


def test_passing_verifiers_are_not_shown_as_complaints() -> None:
    feedback = [
        {"agent": "qa", "verdict": "FAIL", "summary": "테스트 2개 실패"},
        {"agent": "security", "verdict": "PASS", "summary": ""},
    ]
    prompt = task_prompt("계산기", 2, feedback)
    assert "테스트 2개 실패" in prompt
    assert "security" not in prompt
