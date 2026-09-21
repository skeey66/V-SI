from llm_agent.roles import ROLES, system_prompt, task_prompt


def test_four_roles_exist_with_sp1_artifact_kinds() -> None:
    """SP1 의 `kind` 를 그대로 쓴다 — 오케스트레이터가 이 값으로 분기한다."""
    assert ROLES["planner"].artifact_kind == "requirements"
    assert ROLES["dev"].artifact_kind == "source_code"
    assert ROLES["qa"].artifact_kind == "test_report"
    assert ROLES["security"].artifact_kind == "security_report"


def test_verifiers_are_qa_and_security_only() -> None:
    """`is_verifier` 는 "환류를 만드는 역할인가"다 — 오케스트레이터의
    `VERIFIERS` 와 같은 집합이어야 한다. 기획이 판정 도구를 갖게 됐지만
    검증자는 아니다: 기획의 FAIL 은 환류(개발 재시도)가 아니라 승인 대기를
    만든다."""
    assert ROLES["qa"].is_verifier
    assert ROLES["security"].is_verifier
    assert not ROLES["dev"].is_verifier
    assert not ROLES["planner"].is_verifier


def test_verdict_tool_ownership() -> None:
    """`verdict_tool` 은 "판정 근거를 도구에서 받아야 하는가"다 —
    `is_verifier` 와 다른 질문이다. 기획은 검증자가 아니면서 판정 도구를 갖는
    유일한 역할이고, 그래서 파일도 쓰면서 종료코드로 판정도 받는다."""
    assert ROLES["qa"].verdict_tool == "run_tests"
    assert ROLES["security"].verdict_tool == "run_security_scan"
    assert ROLES["planner"].verdict_tool == "check_acceptance_tests"
    assert ROLES["dev"].verdict_tool is None
    # 판정 도구는 반드시 그 역할이 실제로 가진 도구여야 한다 — 아니면 루프가
    # 영영 못 받는 도구를 재촉하다 실패한다.
    for role in ROLES.values():
        if role.verdict_tool is not None:
            assert role.verdict_tool in role.tools


def test_dev_prompt_says_tests_are_not_writable() -> None:
    """개발이 인수 테스트를 덮어쓰는 것은 도구 서버가 막는다. 프롬프트도 그
    사실을 알려줘야 모델이 거부당한 쓰기를 반복하며 턴을 태우지 않는다."""
    assert "쓸 수 없다" in system_prompt(ROLES["dev"])


def test_planner_prompt_demands_the_verdict_tool() -> None:
    """기획이 판정 도구를 안 부르면 루프가 실패시킨다 — 프롬프트가 먼저
    알려줘야 한다."""
    prompt = system_prompt(ROLES["planner"])
    assert "check_acceptance_tests" in prompt
    assert "실패하는 것이 정상" in prompt


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
