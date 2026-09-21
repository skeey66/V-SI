"""에이전트 진입점. 모드에 따라 실행기를 고른다.

기본값이 `stub` 인 것은 의도다 — SP1 의 144개 테스트가 그대로 돌아야 하고
(스펙 §4.1/§12.1), 새 기본값이 조용히 LLM 을 부르는 일이 없어야 한다.

**브리프(Task 12 계획) 대비 실제 인터페이스 차이:**

브리프는 MCP 엔드포인트가 하나(`http://workspace:8000/mcp`)라고 가정했지만,
Task 3 이 실제로 만든 `services/tool_server/main.py`는 역할별로 별도
`MCPServer`를 `/mcp/<role>/`(끝 슬래시 포함)에 각각 마운트한다 — 한 에이전트가
다른 역할의 경로에 잘못 연결되면 `LlmExecutor.run_task`가 `McpRoleMismatch`로
요란하게 죽는다(`packages/llm_agent/executor.py`). 그래서 여기서는 고정
URL이 아니라 `AGENT`를 엮어 넣은 역할별 기본값을 만든다.
"""
from __future__ import annotations

import os

from a2a.server.tasks import DatabaseTaskStore
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_runtime.app import create_agent_app
from agent_runtime.card import build_card
from agent_runtime.telemetry import setup_tracing
from llm_agent.executor import LlmExecutor
from llm_agent.roles import ROLES
from orchestrator.db import make_engine
from stub_agent.executor import ARTIFACT_KIND, StubExecutor
from stub_agent.scenario import AgentScenario

DEFAULT_MODE = "stub"


class UnknownMode(ValueError):
    """`VSI_AGENT_MODE` 가 stub 도 llm 도 아니다."""


def default_mcp_url(agent: str) -> str:
    """`VSI_MCP_URL`이 없을 때 이 에이전트가 붙을 기본 MCP 엔드포인트.

    도구 서버는 역할별로 `/mcp/<role>/`(끝 슬래시 포함)에 따로 마운트된다
    (`services/tool_server/main.py`) — 모든 에이전트가 같은 URL을 보면
    안 되고, 반드시 자기 자신의 역할 경로를 봐야 한다. 이 조합 로직은 원래
    아래 `if "VSI_AGENT" in os.environ:` 가드 안에 인라인 f-string으로만
    있었다 — uvicorn이 실제로 앱을 부팅할 때만 실행되고, 벌거벗은 `import
    agent_entry.main`으로 도는 단위 테스트에서는 전혀 실행되지 않아 아무
    시험도 이 조합을 검증하지 못했다. 이 배선이 실제로 어긋난 채(예: 모든
    에이전트가 같은 URL로 붙음) 배포된 적이 있다 — 실측: 첫 디스패치에서
    4개 에이전트 전부 HTTP 421을 받은 라이브 장애. 순수 함수로 뽑아
    가드 밖에서 직접 부를 수 있게 한다."""
    return f"http://workspace:8000/mcp/{agent}/"


def build_executor(
    mode: str,
    agent: str,
    *,
    scenario_path: str,
    ollama_url: str,
    model: str,
    mcp_url: str,
    session_maker,
):
    if mode == "stub":
        return StubExecutor(AgentScenario.from_yaml(scenario_path, agent), agent)
    if mode == "llm":
        return LlmExecutor(
            agent=agent, ollama_url=ollama_url, model=model,
            mcp_url=mcp_url, session_maker=session_maker,
        )
    raise UnknownMode(f"{mode!r} — stub 또는 llm 이어야 한다")


# 아래 배선은 `VSI_AGENT`가 있을 때만 돈다 — uvicorn이 `agent_entry.main:app`을
# 부팅할 때는 항상 있지만(compose가 4개 에이전트 모두에 채워 준다), 단위
# 테스트는 `build_executor`/`UnknownMode`/`DEFAULT_MODE`만 임포트하고 앱을
# 조립하지 않는다(SP1의 진입점과 동일한 전제 — 그 모듈도 실제
# 컨테이너 기동으로만 검증했지 벌거벗은 `import`로 단위 테스트하지 않았다).
# 이 가드가 없으면 `os.environ["VSI_AGENT"]`가 테스트 프로세스에서 즉시
# `KeyError`를 던져 `build_executor`조차 임포트할 수 없다.
if "VSI_AGENT" in os.environ:
    AGENT = os.environ["VSI_AGENT"]
    MODE = os.environ.get("VSI_AGENT_MODE", DEFAULT_MODE)
    BASE_URL = os.environ["VSI_BASE_URL"]
    DB_URL = os.environ["VSI_DATABASE_URL"]

    # `setup_tracing`은 `create_agent_app`의 `instrument_app`/`instrumented_client`
    # 보다 먼저 불러야 한다 — 계측 라이브러리는 계측 시점에 전역 TracerProvider를
    # 얻어 고정하므로, 순서가 뒤집히면 실제 익스포터 없는 기본 provider에 묶인다.
    # `VSI_OTLP_ENDPOINT`가 없으면(예: 로컬 단위 테스트) 익스포트 없이 조용히 넘어간다.
    setup_tracing(AGENT, endpoint=os.environ.get("VSI_OTLP_ENDPOINT"))

    engine = make_engine(DB_URL)
    card = build_card(
        name=AGENT,
        description=f"{AGENT} 에이전트 ({MODE})",
        skills=[ROLES[AGENT].artifact_kind if MODE == "llm" else ARTIFACT_KIND[AGENT]],
        base_url=BASE_URL,
    )
    # `create_table=False`가 핵심이다. 기본값(True)은 첫 사용 시점에 지연 CREATE TABLE을
    # 하는데, 에이전트 4기가 한 DB를 공유하므로 qa·security 병렬 디스패치에서 네 프로세스가
    # 동시에 DDL을 때려 "relation already exists"가 날 수 있다. 테이블 생성은 오케스트레이터
    # startup이 단독으로 수행한다(`orchestrator/main.py`) — 에이전트가 이 테이블을 쓰는
    # 유일한 계기가 그 오케스트레이터의 디스패치이므로 순서는 보장된다.
    task_store = DatabaseTaskStore(engine=engine, create_table=False, table_name="tasks")

    executor = build_executor(
        MODE,
        AGENT,
        scenario_path=os.environ.get("VSI_SCENARIO", "scenarios/all_pass.yaml"),
        ollama_url=os.environ.get("VSI_OLLAMA_URL", "http://host.docker.internal:11434"),
        model=os.environ.get("VSI_MODEL", "qwen3:8b"),
        # 역할별 마운트(`/mcp/<role>/`)를 반영한다 — 단일 엔드포인트를 가정하면
        # 잘못된 역할에 연결되고, `LlmExecutor`가 `McpRoleMismatch`로 죽는다.
        mcp_url=os.environ.get("VSI_MCP_URL", default_mcp_url(AGENT)),
        session_maker=async_sessionmaker(engine, expire_on_commit=False),
    )
    app = create_agent_app(card, executor, task_store)
