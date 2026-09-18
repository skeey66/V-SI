"""이벤트 게이트웨이 통합 테스트.

전제: `docker compose up -d --build`로 스택이 떠 있어야 한다(event-gateway
포함, 호스트 포트 8100). 게이트웨이는 오케스트레이터와 분리된 프로세스이므로
이 테스트는 오케스트레이터를 건드리지 않고 게이트웨이 컨테이너와 DB만
상대한다.

`tests/conftest.py`의 `session` 픽스처는 매 테스트마다 스키마를
drop_all/create_all한다 — 살아 있는 컴포즈 스택(오케스트레이터·게이트웨이가
같은 물리 DB를 계속 폴링 중)에 대고 쓰면 스키마가 통째로 사라졌다 돌아오고
`events.event_id` 시퀀스가 리셋된다. 이는 단위 테스트 전용으로 문서화된
픽스처라 여기서는 쓰지 않는다 — `tests/integration/harness.py`의 다른
통합 테스트처럼 `db_session()`으로 직접 세션을 열고 닫는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import websockets
from sqlalchemy import select

from orchestrator.models import OutboxEvent
from tests.integration.harness import db_session

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_WS_URL = os.environ.get("VSI_GATEWAY_WS_URL", "ws://localhost:8100/ws")
CONNECT_TIMEOUT_S = 30.0


def _compose(*args: str) -> None:
    proc = subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args)} 실패 (rc={proc.returncode})\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


async def _connect_with_retry(timeout_s: float = CONNECT_TIMEOUT_S):
    """게이트웨이 컨테이너가 뜨는 도중에도(재기동 직후 등) 연결을 재시도한다."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    last_exc: Exception | None = None
    while loop.time() < end:
        try:
            return await websockets.connect(GATEWAY_WS_URL)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            # 컨테이너가 뜨는 도중에는 포트가 열려도 핸드셰이크가 아직 준비되지
            # 않아 TCP는 붙었다 끊기는 경우가 있다(InvalidMessage 등) — 재시도한다.
            last_exc = exc
            await asyncio.sleep(0.3)
    raise RuntimeError(f"게이트웨이 WS 연결 대기 시간 초과: {last_exc}")


async def _recv_until(ws, aggregate_id: str, timeout_s: float = 10.0) -> dict:
    """다른 테스트/시나리오가 같은 시각에 발행 중인 이벤트가 섞여 들어와도,
    우리가 방금 기록한 이벤트를 찾을 때까지 계속 받는다."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    while loop.time() < end:
        remaining = max(end - loop.time(), 0.1)
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("aggregate_id") == aggregate_id:
            return msg
    raise AssertionError(f"{aggregate_id} 이벤트를 {timeout_s}초 안에 받지 못했다")


async def test_new_outbox_row_reaches_websocket() -> None:
    aggregate_id = f"t-{uuid.uuid4()}"
    ws = await _connect_with_retry()
    try:
        async with db_session() as s:
            s.add(
                OutboxEvent(
                    aggregate="task",
                    aggregate_id=aggregate_id,
                    event_type="task_submitted",
                    payload={"agent": "dev"},
                )
            )
            await s.commit()
        msg = await _recv_until(ws, aggregate_id)
    finally:
        await ws.close()

    assert msg["event_type"] == "task_submitted"
    assert msg["payload"]["agent"] == "dev"
    assert msg["aggregate"] == "task"


async def test_published_at_is_marked() -> None:
    aggregate_id = f"t-{uuid.uuid4()}"
    ws = await _connect_with_retry()
    try:
        async with db_session() as s:
            s.add(
                OutboxEvent(
                    aggregate="task",
                    aggregate_id=aggregate_id,
                    event_type="task_completed",
                    payload={},
                )
            )
            await s.commit()
        await _recv_until(ws, aggregate_id)
    finally:
        await ws.close()

    async with db_session() as s:
        rows = (
            await s.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].published_at is not None


async def test_restart_does_not_lose_events_written_while_down() -> None:
    """아웃박스 설계가 정당화되는 지점: 게이트웨이가 죽어 있는 동안 기록된
    이벤트도 재기동 후 이어서 테일되어 연결된 클라이언트에 도달해야 한다.

    `published_at`이 NULL인 행은 게이트웨이 없이도 DB에 그대로 남는다 —
    재기동한 프로세스가 "마지막으로 발행한 지점 이후"부터 다시 훑기 시작하면
    유실 없이 따라잡는다.
    """
    aggregate_id = f"t-{uuid.uuid4()}"
    try:
        _compose("kill", "-s", "SIGKILL", "event-gateway")

        # 게이트웨이가 죽어 있는 동안 이벤트를 기록한다 — published_at은 NULL로 남는다.
        async with db_session() as s:
            s.add(
                OutboxEvent(
                    aggregate="task",
                    aggregate_id=aggregate_id,
                    event_type="task_failed",
                    payload={"agent": "qa", "verdict": "FAIL", "failure_class": "assertion"},
                )
            )
            await s.commit()

        async with db_session() as s:
            rows = (
                await s.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)
                )
            ).scalars().all()
        assert rows[0].published_at is None, "게이트웨이가 죽어 있는 동안은 발행되면 안 된다"

        _compose("up", "-d", "event-gateway")

        ws = await _connect_with_retry()
        try:
            msg = await _recv_until(ws, aggregate_id, timeout_s=15.0)
        finally:
            await ws.close()

        assert msg["event_type"] == "task_failed"
        assert msg["payload"]["verdict"] == "FAIL"
        assert msg["payload"]["failure_class"] == "assertion"

        async with db_session() as s:
            rows = (
                await s.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)
                )
            ).scalars().all()
        assert rows[0].published_at is not None
    finally:
        # 다음 테스트를 위해 게이트웨이가 확실히 살아 있게 한다.
        _compose("up", "-d", "event-gateway")
