"""오케스트레이터 서비스.

컨테이너로 떠 있어야 하는 이유는 둘이다: 에이전트가 푸시 콜백을 보낼 주소가
있어야 하고(`VSI_PUSH_URL`), Task 13의 SIGKILL 테스트가 이 컨테이너를 죽였다
살린다.

OTel 계측(Task 14): `setup_tracing`은 이 모듈에서 다른 무엇보다도 먼저
불러야 한다 — 아래에서 만드는 공유 httpx 클라이언트(`http`, 모든 에이전트로
나가는 submit/get_task 호출에 쓰인다)를 계측된 클라이언트로 만들고, 계측
라이브러리가 계측 시점에 고정하는 전역 TracerProvider가 실제 익스포터를 갖고
있어야 하기 때문이다. `instrument_app(app)`은 이 파일 맨 끝, 라우트를 모두
붙인 뒤에 부른다 — 순서가 바뀌면 이 서비스에 붙은 라우트가 계측 대상에서
빠진다.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from a2a.server.tasks import DatabaseTaskStore

from agent_runtime.telemetry import instrument_app, instrumented_client, setup_tracing
from orchestrator.a2a_client import AgentClient
from orchestrator.db import make_engine, session_factory
from orchestrator.engine import DuplicateRequirement, WorkflowEngine
from orchestrator.models import Base
from orchestrator.policy import TimeoutConfig
from orchestrator.push_receiver import create_push_router
from orchestrator.reconciler import (
    DEFAULT_INTERVAL_S,
    DEFAULT_STALE_AFTER_S,
    DEFAULT_STUCK_AFTER_S,
    Reconciler,
)
from orchestrator.workflow import RequirementState

logging.basicConfig(level=os.environ.get("VSI_LOG_LEVEL", "INFO"))
logger = logging.getLogger("orchestrator")

#: 컴포즈 네트워크 안에서는 서비스 이름이 곧 호스트 이름이고 포트는 전부 8000이다.
AGENTS = {"planner": 8000, "dev": 8000, "qa": 8000, "security": 8000}

PUSH_TOKEN = os.environ["VSI_PUSH_TOKEN"]
PUSH_URL = os.environ["VSI_PUSH_URL"]
DB_URL = os.environ["VSI_DATABASE_URL"]

# 다른 무엇보다 먼저: 아래 `instrumented_client(...)`가 이 시점의 전역
# TracerProvider를 고정한다(모듈 docstring 참고).
setup_tracing("orchestrator", endpoint=os.environ.get("VSI_OTLP_ENDPOINT"))

timeouts = TimeoutConfig.from_env(os.environ)
engine_db = make_engine(DB_URL)
maker = session_factory(engine_db)
# 공유 httpx 클라이언트의 타임아웃은 **요청 하나**의 상한이다 — 워크플로 단계
# 예산(`step_s`, 15분)을 여기 쓰면 층이 어긋난다. 한 단계는 제출 + 여러 번의
# 폴링 왕복으로 이루어지므로, 그 단계 전체 예산을 개별 왕복의 상한으로 쓰면
# 왕복 하나가 단계 전체를 먹어 치울 수 있다. 실제로 그랬다: 멈춘 에이전트
# 하나가 리컨실리에이션 루프(직렬이다)를 최대 15분 붙잡았다.
# 여기는 AgentExecutor 층(`executor_s`)의 backstop이고, 각 호출부는 그보다
# 훨씬 짧은 `tool_s`로 스스로를 감싼다(`engine.dispatch_agent`/`refresh_task`).
http = instrumented_client(timeout_s=timeouts.executor_s)

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
reconciler = Reconciler(
    maker,
    workflow,
    interval_s=float(os.environ.get("VSI_RECONCILE_INTERVAL_S", DEFAULT_INTERVAL_S)),
    stale_after_s=float(os.environ.get("VSI_RECONCILE_STALE_S", DEFAULT_STALE_AFTER_S)),
    # 리뷰 라운드 1: PROBE로도 안 끝나는 행(SDK 결함) 안전망. 운영 기본값은
    # stale_after_s보다 한 자릿수 이상 크게 잡아 정상적으로 느린 실행을
    # 강제로 끊지 않는다.
    stuck_after_s=float(os.environ.get("VSI_RECONCILE_STUCK_S", DEFAULT_STUCK_AFTER_S)),
)

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
    try:
        await workflow.start(body.requirement_id, body.title, body.run_id)
    except DuplicateRequirement:
        # 데모의 정문이다. 같은 명령을 두 번 치는 것은 흔한 일이고, 그건
        # 호출자의 상태 충돌(409)이지 서버 고장(500)이 아니다.
        raise HTTPException(
            status_code=409, detail="requirement already exists"
        ) from None
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


# 라우트를 모두 붙인 뒤에 계측한다 — 위 /push, /requirements, /healthz가 전부
# 등록된 지금이 그 시점이다. `@app.on_event` 훅은 라우트가 아니라 순서와
# 무관하다.
instrument_app(app)


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

    # 리컨실리에이션 루프(Task 13)는 평시에도 돈다. 크래시 복구가 별도의 경로가
    # 아니라 **항상 돌고 있는 같은 루프**여야, 재기동 직후에도 특별히 할 일이 없다 —
    # 그냥 다음 주기에 현재 상태를 보고 이어서 수렴시킨다.
    _spawn(reconciler.run_forever(), "reconciler")


@app.on_event("shutdown")
async def _shutdown() -> None:
    for task in list(_background):
        task.cancel()
    await http.aclose()
    await engine_db.dispose()
