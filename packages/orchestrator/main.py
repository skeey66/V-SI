"""오케스트레이터 서비스.

컨테이너로 떠 있어야 하는 이유는 둘이다: 에이전트가 푸시 콜백을 보낼 주소가
있어야 하고(`VSI_PUSH_URL`), Task 13의 SIGKILL 테스트가 이 컨테이너를 죽였다
살린다.

OTel 계측은 Task 14 소관이라 여기엔 없다 — `agent_runtime.telemetry`는 아직
존재하지 않으므로 임포트하면 부팅이 깨진다.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from a2a.server.tasks import DatabaseTaskStore

from orchestrator.a2a_client import AgentClient
from orchestrator.db import make_engine, session_factory
from orchestrator.engine import WorkflowEngine
from orchestrator.models import Base
from orchestrator.policy import TimeoutConfig
from orchestrator.push_receiver import create_push_router
from orchestrator.workflow import RequirementState

logging.basicConfig(level=os.environ.get("VSI_LOG_LEVEL", "INFO"))
logger = logging.getLogger("orchestrator")

#: 컴포즈 네트워크 안에서는 서비스 이름이 곧 호스트 이름이고 포트는 전부 8000이다.
AGENTS = {"planner": 8000, "dev": 8000, "qa": 8000, "security": 8000}

PUSH_TOKEN = os.environ["VSI_PUSH_TOKEN"]
PUSH_URL = os.environ["VSI_PUSH_URL"]
DB_URL = os.environ["VSI_DATABASE_URL"]

timeouts = TimeoutConfig.from_env(os.environ)
engine_db = make_engine(DB_URL)
maker = session_factory(engine_db)
http = httpx.AsyncClient(timeout=timeouts.step_s)

clients = {
    name: AgentClient(
        base_url=f"http://{name}:{port}",
        httpx_client=http,
        push_url=PUSH_URL,
        push_token=PUSH_TOKEN,
    )
    for name, port in AGENTS.items()
}
workflow = WorkflowEngine(maker, clients, timeouts)

app = FastAPI(title="v-si orchestrator")

#: 백그라운드 처리 태스크의 강한 참조. 없으면 GC가 실행 중인 태스크를 수거한다.
_background: set[asyncio.Task] = set()


def _spawn(coro, label: str) -> None:
    task = asyncio.create_task(coro)
    task.set_name(label)
    _background.add(task)

    def _done(t: asyncio.Task) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.exception("%s 처리 실패", label, exc_info=t.exception())

    task.add_done_callback(_done)


async def _on_push(body: dict) -> None:
    """푸시 콜백은 즉시 202로 응답하고 실제 처리는 백그라운드로 넘긴다.

    에이전트는 이 POST가 끝날 때까지 자신의 이벤트 소비 루프를 붙잡고 기다린다.
    여기서 파이프라인 다음 단계 전체를 동기로 돌면 그 대기가 연쇄적으로 길어지고,
    에이전트 쪽 푸시 타임아웃에 걸려 "성공했는데 실패로 로깅되는" 상태가 된다.
    유실은 Task 13의 리컨실리에이션이 덮는다.
    """
    _spawn(workflow.on_push_notification(body), "push")


app.include_router(create_push_router(token=PUSH_TOKEN, on_notification=_on_push))


class StartRequirement(BaseModel):
    requirement_id: str
    title: str
    run_id: str


@app.post("/requirements", status_code=202)
async def start_requirement(body: StartRequirement) -> dict[str, str]:
    await workflow.start(body.requirement_id, body.title, body.run_id)
    return {"requirement_id": body.requirement_id, "accepted": "true"}


@app.get("/requirements/{requirement_id}")
async def get_requirement(requirement_id: str) -> dict[str, str]:
    try:
        state: RequirementState = await workflow.state_of(requirement_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="unknown requirement") from None
    return {"requirement_id": requirement_id, "state": state.value}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
async def _startup() -> None:
    """스키마 소유자는 오케스트레이터 한 곳이다.

    `DatabaseTaskStore`는 기본적으로 첫 사용 시점에 지연 CREATE TABLE을 한다
    (`create_table=True`). 에이전트 4기가 같은 DB를 공유하므로 첫 병렬 디스패치에서
    네 프로세스가 동시에 `create_all`을 때리면 checkfirst와 실제 CREATE 사이가
    벌어져 "relation already exists"가 난다. 에이전트 쪽 지연 생성을 끄고
    (`stub_agent/main.py`의 `create_table=False`) 여기서 한 번만 만들어
    경합 자체를 없앤다. 에이전트가 이 테이블을 건드리는 유일한 계기는 우리가 보낸
    디스패치이고, 디스패치는 이 startup 훅이 끝난 뒤에야 가능하므로 순서도 성립한다.
    """
    async with engine_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # SDK 소유 테이블은 SDK 자신의 마이그레이션 경로로 만든다 — 스키마를 우리가
    # 베껴 쓰면 SDK 업그레이드 때 조용히 어긋난다.
    await DatabaseTaskStore(
        engine=engine_db, create_table=True, table_name="tasks"
    ).initialize()
    logger.info("스키마 준비 완료")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await http.aclose()
    await engine_db.dispose()
