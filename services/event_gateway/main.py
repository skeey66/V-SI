"""이벤트 게이트웨이 — 아웃박스를 WebSocket으로 팬아웃한다.

오케스트레이터와 **별도 프로세스**로 뜬다. UI가 죽거나 WebSocket이 밀려도
워크플로 자체는 이 프로세스와 무관하게 계속 돈다 — 그게 이 분리의 존재
이유다. 그래서 이 모듈은 오케스트레이터 코드를 import하지 않고, 자기
자신의 DB 연결·자기 자신의 FastAPI 앱만 가진다(스키마는 여전히
오케스트레이터가 소유 — 여기서는 `Base.metadata.create_all`을 부르지 않는다).

폴링·전달 로직(커서 재개, at-least-once 전달)은 `event_gateway.pump`에
있다 — 이 파일은 FastAPI 배선(라우트, lifespan, OTel 계측)만 맡는다. 둘을
가른 이유: 이 파일은 import 시점에 `VSI_DATABASE_URL` 환경변수를 필수로
읽고 OTel 전역 TracerProvider를 고정한다(`setup_tracing`) — 둘 다 실제
컨테이너 실행 환경 밖에서, 특히 같은 pytest 프로세스 안에서 다른 테스트와
공유되는 전역 상태를 오염시키는 부작용이다(리뷰 라운드 1에서 실측: 이
모듈을 단위 시험이 직접 import하자 `tests/agent_runtime/test_telemetry.py`가
자기 TracerProvider를 못 심고 조용히 깨졌다). `pump.py`는 이런 부작용이
전혀 없어 단위 시험이 안전하게 직접 부를 수 있다.

재시작 복구: `pump.resume_position`이 시작 시 `published_at IS NOT NULL`인
행 중 가장 큰 `event_id`를 찾아 그 지점부터 이어서 테일한다. 게이트웨이가
죽어 있던 동안 쌓인 행은 `published_at`이 NULL인 채로 DB에 남아 있으므로
`event_id > last_id` 조건에 그대로 걸려 유실 없이 따라잡는다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from agent_runtime.telemetry import instrument_app, setup_tracing
from orchestrator.db import make_engine, session_factory
from services.event_gateway.pump import pump_once, resume_position, tail_outbox

# `tail_outbox`를 여기로도 재노출한다 — 브리프가 명시한 공개 인터페이스
# (`tail_outbox(session_maker, last_id, limit=100) -> list[dict]`)가 이
# 모듈에 있다는 기대를 깨지 않기 위해서다.
__all__ = ["app", "tail_outbox"]

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


async def _pump() -> None:
    """마지막 published 지점 이후부터 이어서 테일하는 상시 루프.

    한 사이클에서 예외가 나도(일시적 DB 단절 등) 루프 자체는 죽지 않는다 —
    로그를 남기고 다음 폴에서 같은 last_id부터 재시도한다.
    """
    last_id = await resume_position(_maker)
    logger.info("아웃박스 테일 시작: last_id=%d", last_id)
    while True:
        try:
            last_id = await pump_once(_maker, _clients, last_id)
        except Exception:
            logger.exception("아웃박스 폴링 실패, %.1f초 뒤 재시도", POLL_S)
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
