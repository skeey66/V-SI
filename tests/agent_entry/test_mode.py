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


def test_default_mcp_url_composition_mounts_the_agents_own_role() -> None:
    """`main.py`가 `VSI_MCP_URL`이 없을 때 각 에이전트에 채워 주는 기본
    MCP URL — 역할별로 `/mcp/<role>/` 에 마운트된 서버(`services/tool_server/
    main.py`)와 일치해야 한다. 이 배선이 실제로 어긋난 채(모든 에이전트가
    같은 URL을 봄) 배포된 적이 있다 — 실측: 첫 디스패치에서 4개 에이전트
    전부 HTTP 421을 받은 라이브 장애. 이 조합 로직은 지금 `if "VSI_AGENT"
    in os.environ:` 이라는 모듈 최상단 가드 안에서만 돌아서(uvicorn 이
    `agent_entry.main:app` 을 부팅할 때만 실행), 벌거벗은 `import` 로는 전혀
    실행되지 않는다 — 그래서 순수 함수로 뽑아 직접 부른다."""
    from agent_entry.main import default_mcp_url

    assert default_mcp_url("dev") == "http://workspace:8000/mcp/dev/"
    assert default_mcp_url("qa") == "http://workspace:8000/mcp/qa/"
    assert default_mcp_url("planner") == "http://workspace:8000/mcp/planner/"
    assert default_mcp_url("security") == "http://workspace:8000/mcp/security/"
