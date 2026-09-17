from __future__ import annotations
import os

from a2a.server.tasks import DatabaseTaskStore

from agent_runtime.app import create_agent_app
from agent_runtime.card import build_card
from orchestrator.db import make_engine
from stub_agent.executor import ARTIFACT_KIND, StubExecutor
from stub_agent.scenario import AgentScenario

# uvicorn이 부팅 시 이 모듈을 임포트한다(Task 8). 여기서는 임포트가 깨지지
# 않는지만 보장한다 — 실제 기동/DB 연결 검증은 Task 8의 몫이다.
AGENT = os.environ["VSI_AGENT"]
SCENARIO = os.environ.get("VSI_SCENARIO", "scenarios/qa_fails_twice.yaml")
BASE_URL = os.environ["VSI_BASE_URL"]
DB_URL = os.environ["VSI_DATABASE_URL"]

scenario = AgentScenario.from_yaml(SCENARIO, AGENT)
card = build_card(
    name=AGENT,
    description=f"{AGENT} 스텁 에이전트",
    skills=[ARTIFACT_KIND[AGENT]],
    base_url=BASE_URL,
)
task_store = DatabaseTaskStore(engine=make_engine(DB_URL), table_name="tasks")
app = create_agent_app(card, StubExecutor(scenario, AGENT), task_store)
