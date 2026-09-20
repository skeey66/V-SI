from orchestrator.reconciler import DEFAULT_STUCK_AFTER_S


def test_stuck_ceiling_clears_the_agent_budget() -> None:
    """에이전트 예산(600초)보다 커야 정상 작업이 학살당하지 않는다 (스펙 §9.2)."""
    from llm_agent.loop import BUDGET_S

    assert DEFAULT_STUCK_AFTER_S > BUDGET_S
    assert DEFAULT_STUCK_AFTER_S == 900.0


def test_turn_cap_and_budget_match_the_spec() -> None:
    from llm_agent.loop import BUDGET_S, MAX_TURNS

    assert MAX_TURNS == 12
    assert BUDGET_S == 600.0
