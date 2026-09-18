from __future__ import annotations
import os

from a2a.server.tasks import DatabaseTaskStore

from agent_runtime.app import create_agent_app
from agent_runtime.card import build_card
from agent_runtime.telemetry import setup_tracing
from orchestrator.db import make_engine
from stub_agent.executor import ARTIFACT_KIND, StubExecutor
from stub_agent.scenario import AgentScenario

# uvicorn이 부팅 시 이 모듈을 임포트한다(Task 8). 여기서는 임포트가 깨지지
# 않는지만 보장한다 — 실제 기동/DB 연결 검증은 Task 8의 몫이다.
AGENT = os.environ["VSI_AGENT"]
SCENARIO = os.environ.get("VSI_SCENARIO", "scenarios/qa_fails_twice.yaml")
BASE_URL = os.environ["VSI_BASE_URL"]
DB_URL = os.environ["VSI_DATABASE_URL"]

# `setup_tracing`은 `create_agent_app`의 `instrument_app`/`instrumented_client`
# 보다 먼저 불러야 한다 — 계측 라이브러리는 계측 시점에 전역 TracerProvider를
# 얻어 고정하므로, 순서가 뒤집히면 실제 익스포터 없는 기본 provider에 묶인다.
# `VSI_OTLP_ENDPOINT`가 없으면(예: 로컬 단위 테스트) 익스포트 없이 조용히 넘어간다.
setup_tracing(AGENT, endpoint=os.environ.get("VSI_OTLP_ENDPOINT"))

scenario = AgentScenario.from_yaml(SCENARIO, AGENT)
card = build_card(
    name=AGENT,
    description=f"{AGENT} 스텁 에이전트",
    skills=[ARTIFACT_KIND[AGENT]],
    base_url=BASE_URL,
)
# `create_table=False`가 핵심이다. 기본값(True)은 첫 사용 시점에 지연 CREATE TABLE을
# 하는데, 에이전트 4기가 한 DB를 공유하므로 qa·security 병렬 디스패치에서 네 프로세스가
# 동시에 DDL을 때려 "relation already exists"가 날 수 있다. 테이블 생성은 오케스트레이터
# startup이 단독으로 수행한다(`orchestrator/main.py`) — 에이전트가 이 테이블을 쓰는
# 유일한 계기가 그 오케스트레이터의 디스패치이므로 순서는 보장된다.
task_store = DatabaseTaskStore(
    engine=make_engine(DB_URL), create_table=False, table_name="tasks"
)
app = create_agent_app(card, StubExecutor(scenario, AGENT), task_store)
