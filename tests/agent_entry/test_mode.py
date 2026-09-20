import pytest

from agent_entry.main import UnknownMode, build_executor
from llm_agent.executor import LlmExecutor
from stub_agent.executor import StubExecutor

KW = dict(scenario_path="scenarios/all_pass.yaml", ollama_url="http://x",
          model="qwen3:8b", mcp_url="http://y/mcp", session_maker=None)


def test_stub_mode_keeps_sp1_behaviour() -> None:
    assert isinstance(build_executor("stub", "dev", **KW), StubExecutor)


def test_llm_mode_builds_llm_executor() -> None:
    assert isinstance(build_executor("llm", "dev", **KW), LlmExecutor)


def test_unknown_mode_is_rejected_loudly() -> None:
    with pytest.raises(UnknownMode):
        build_executor("magic", "dev", **KW)


def test_default_mode_is_stub() -> None:
    """기본값이 stub 인 것은 의도다 — SP1 의 151개 테스트가 그대로 돌아야 한다."""
    from agent_entry.main import DEFAULT_MODE

    assert DEFAULT_MODE == "stub"
