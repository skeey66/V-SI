"""이벤트 게이트웨이 — 아웃박스를 WebSocket으로 팬아웃한다.

오케스트레이터와 **별도 프로세스**로 뜬다. UI가 죽거나 WebSocket이 밀려도
워크플로 자체는 이 프로세스와 무관하게 계속 돈다 — 그게 이 분리의 존재
이유다. 그래서 이 모듈은 오케스트레이터 코드를 import하지 않고, 자기
자신의 DB 연결·자기 자신의 FastAPI 앱만 가진다(스키마는 여전히
오케스트레이터가 소유 — 여기서는 `Base.metadata.create_all`을 부르지 않는다).

재시작 복구: `_resume_position`이 시작 시 `published_at IS NOT NULL`인 행 중
가장 큰 `event_id`를 찾아 그 지점부터 이어서 테일한다. 게이트웨이가 죽어
있던 동안 쌓인 행은 `published_at`이 NULL인 채로 DB에 남아 있으므로
`event_id > last_id` 조건에 그대로 걸려 유실 없이 따라잡는다. 이미 발행한
행을 다시 보내지 않는 것도 같은 커서 덕분이다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from agent_runtime.telemetry import instrument_app, setup_tracing
from orchestrator.db import make_engine, session_factory
from orchestrator.models import OutboxEvent

logging.basicConfig(level=os.environ.get("VSI_LOG_LEVEL", "INFO"))
logger = logging.getLogger("event_gateway")

POLL_S = 0.5

# OTel(Task 14와 동일한 방식): 이 서비스는 나가는 HTTP 호출이 없으므로
# `instrumented_client`는 쓰지 않는다 — 받는 쪽(WS 업그레이드 요청)만
# 계측해도 브리프가 요구하는 범위를 넘는 선택 사항이라 값싸게 넣는다.
# `setup_tracing`은 다른 무엇보다 먼저 불러야 전역 TracerProvider가
# 실제 익스포터를 갖는다(agent_runtime/telemetry.py 참고).
setup_tracing("event-gateway", endpoint=os.environ.get("VSI_OTLP_ENDPOINT"))

_engine = make_engine(os.environ["VSI_DATABASE_URL"])
_maker = session_factory(_engine)
_clients: set[WebSocket] = set()


async def _resume_position(session_maker) -> int:
    """마지막으로 발행한 이벤트의 event_id. 발행 이력이 없으면 0.

    재시작 시 여기서부터 이어서 테일해야, 게이트웨이가 죽어 있던 동안 쌓인
    (미발행) 이벤트는 놓치지 않으면서 이미 내보낸 이벤트를 중복 재전송하지
    않는다.
    """
    async with session_maker() as s:
        max_id = (
            await s.execute(
                select(func.max(OutboxEvent.event_id)).where(
                    OutboxEvent.published_at.is_not(None)
                )
            )
        ).scalar()
        return max_id or 0


async def tail_outbox(session_maker, last_id: int, limit: int = 100) -> list[dict]:
    """`last_id`보다 큰 아웃박스 행을 읽고, 같은 트랜잭션에서 published_at을 찍는다.

    본문(payload)은 그대로 통과시킨다 — 가공·요약·필터링하지 않는다. UI와
    수용 테스트가 이 필드들을 그대로 읽는다.
    """
    async with session_maker() as s:
        rows = (
            await s.execute(
                select(OutboxEvent)
                .where(OutboxEvent.event_id > last_id)
                .order_by(OutboxEvent.event_id)
                .limit(limit)
            )
        ).scalars().all()
        out = []
        for r in rows:
            out.append(
                {
                    "event_id": r.event_id,
                    "aggregate": r.aggregate,
                    "aggregate_id": r.aggregate_id,
                    "event_type": r.event_type,
                    "payload": r.payload,
                }
            )
            r.published_at = datetime.now(timezone.utc)
        await s.commit()
        return out


async def _pump() -> None:
    """마지막 published 지점 이후부터 이어서 테일하는 상시 루프.

    연결된 클라이언트가 하나도 없으면 이번 사이클은 그냥 건너뛴다 —
    `tail_outbox`를 부르지 않으니 `published_at`도 찍히지 않고 커서(`last_id`)도
    그대로다. 그래야 재기동 직후 클라이언트가 붙기 전에 백로그를 아무도 없이
    "발행"해버려서, 뒤늦게 연결한 클라이언트가 그 사이 쌓인 이벤트를 영영 못
    받는 사고를 막는다. 대가는 UI 쪽 지연뿐이다 — 클라이언트가 붙는 순간
    다음 폴에서 밀린 이벤트가 한꺼번에 나간다.

    한 사이클에서 예외가 나도(일시적 DB 단절 등) 루프 자체는 죽지 않는다 —
    로그를 남기고 다음 폴에서 같은 last_id부터 재시도한다.
    """
    last_id = await _resume_position(_maker)
    logger.info("아웃박스 테일 시작: last_id=%d", last_id)
    while True:
        if not _clients:
            await asyncio.sleep(POLL_S)
            continue
        try:
            events = await tail_outbox(_maker, last_id)
        except Exception:
            logger.exception("아웃박스 폴링 실패, %.1f초 뒤 재시도", POLL_S)
            await asyncio.sleep(POLL_S)
            continue
        for event in events:
            last_id = event["event_id"]
            for ws in list(_clients):
                try:
                    await ws.send_json(event)
                except Exception:
                    _clients.discard(ws)
        await asyncio.sleep(POLL_S)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_pump(), name="outbox-pump")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _engine.dispose()


app = FastAPI(title="v-si event gateway", lifespan=_lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


# 라우트를 모두 붙인 뒤에 계측한다(orchestrator/main.py와 동일한 순서 규칙).
instrument_app(app)
