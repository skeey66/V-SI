"""아웃박스 폴링·전달 순수 로직.

`main.py`(FastAPI 앱, OTel 계측, 환경변수 필수 요구)와 일부러 분리했다 —
이 모듈은 DB 세션 메이커와 WebSocket 비슷한 객체(`send_json`만 있으면 됨)만
받으면 되므로, 실제 FastAPI 앱이나 OTel 전역 TracerProvider 없이도 단위
시험할 수 있다. `main.py`를 직접 import하면 모듈 최상단의 `setup_tracing(...)`
호출이 OpenTelemetry의 전역 TracerProvider를 (익스포터 없이) 고정해 버려서,
같은 pytest 프로세스 안에서 나중에 실행되는 `tests/agent_runtime/test_telemetry.py`
가 자기 TracerProvider를 심으려 할 때 "Overriding of current TracerProvider is
not allowed"로 조용히 무시당하는 문제가 있었다(리뷰 라운드 1에서 발견) — 순수
로직을 여기로 빼서 단위 시험이 그 부작용을 절대 건드리지 않게 한다.

전달 순서(리뷰 라운드 1로 고침): **먼저 보내고, 성공한 것만 나중에
표시한다**(at-least-once). 처음 구현은 반대였다 — 읽자마자 `published_at`을
찍고 커밋한 뒤에 소켓으로 보냈는데, 그 커밋과 전송 사이에 게이트웨이가
죽거나 전송이 전부 실패하면 그 행은 영원히 발행됨으로 표시된 채 아무에게도
전달되지 못한 채 사라진다 — 이 태스크가 막으려는 바로 그 유실이 재시작 없이도
일어난다. 지금은 `tail_outbox`가 읽기만 하고, `_deliver`가 실제로 소켓에
쓰고, 그중 **전달에 성공한 행만** `_mark_published`로 표시한다. 대가는
중복이다 — 전달 후 마킹 사이에 죽으면 재시작 때 그 이벤트를 다시 보낸다.
모든 이벤트가 `event_id`를 갖고 있어 클라이언트가 멱등하게 걸러낼 수 있으니,
UI 그래프 입장에서 중복은 무해하고 유실은 무해하지 않다 — 그래서 이 방향을
택한다.

커서 유효성(Task 17에서 추가, 리뷰 라운드 1에서 문구 수정): `last_id`는
프로세스 메모리에만 산다. `resume_position`은 **프로세스 시작 시점**에만
DB를 신뢰하고, 그 뒤로는 런타임 불변식을 전혀 검사하지 않았다 — 그런데 이
프로세스가 살아 있는 동안 커서보다 작은 `event_id`의 행들이 테이블에서
사라지면(전형적으로 둘 중 하나: CI가 매 테스트마다 스키마를
drop_all/create_all하거나, `tests/integration/harness.py`의 `_purge`·
`cli.py`의 `_purge`처럼 **매 시나리오 실행·매 데모 실행마다 아웃박스 행을
지우는 정상 운영 경로**) `event_id > last_id` 조건이 새로 쌓이는 모든 행보다
커서가 앞서 있는 상태가 되어 **그 뒤로 아무 이벤트도 영원히 전달되지 않는다**
— 에러도 로그도 없이 조용히 유실된다. 실측: 스택을 오래 띄워 둔 채로 단위
테스트 스위트를 돌렸더니 살아 있던 게이트웨이의 `last_id`가 168로 남아 있는데
리셋된 테이블의 `max(event_id)`는 102였다. `pump_once`가 매 사이클 테이블의
실제 `max(event_id)`를 커서와 비교해, 커서가 테이블보다 앞서 있으면(이
프로젝트의 시험·데모 하네스가 매번 일으키는 흔한 상황이다 — DB 리셋만이
아니다) `resume_position`을 다시 불러 커서를 되감는다. **되감김 자체는
일상적인 이벤트다**, 장애 신호가 아니다 — 로그 레벨도 그래서 info다(고쳐진
버그가 있다는 뜻이 아니라 "커서가 방금 다시 계산됐다"는 정보성 기록).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import func, select, update

from orchestrator.models import OutboxEvent

logger = logging.getLogger("event_gateway.pump")


class SendsJson(Protocol):
    async def send_json(self, data: dict) -> None: ...


async def resume_position(session_maker) -> int:
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
    """`last_id`보다 큰, 아직 발행되지 않은 아웃박스 행을 읽기만 한다.

    **여기서는 `published_at`을 찍지 않는다** — 마킹은 실제 전달에 성공한
    뒤 `mark_published`가 별도로 한다(모듈 docstring의 at-least-once 설명
    참고). 본문(payload)은 가공·요약·필터링 없이 그대로 돌려준다 — UI와
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
        return [
            {
                "event_id": r.event_id,
                "aggregate": r.aggregate,
                "aggregate_id": r.aggregate_id,
                "event_type": r.event_type,
                "payload": r.payload,
            }
            for r in rows
        ]


async def current_max_id(session_maker) -> int:
    """지금 테이블에 실제로 있는 가장 큰 `event_id`. 없으면 0.

    `resume_position`(발행된 것 중 최댓값)과 달리 발행 여부를 가리지 않는다 —
    "커서가 테이블보다 앞서 있는가"를 판정하려면 테이블에 물리적으로 존재하는
    상한이 필요하다.
    """
    async with session_maker() as s:
        max_id = (await s.execute(select(func.max(OutboxEvent.event_id)))).scalar()
        return max_id or 0


async def recover_if_stale(session_maker, last_id: int) -> int:
    """커서보다 작은 event_id의 행들이 테이블에서 사라졌으면 재개 지점을 다시 계산한다.

    **되감김은 일상적으로 일어난다 — 장애 신호가 아니다.** `last_id`는 테이블에
    실제로 있었던 행에서만 나오므로 그 순간에는 `last_id <= current_max_id`가
    성립하지만, 그 뒤로 커서보다 작은 event_id의 행이 테이블에서 사라지는 경로가
    둘 있다: (1) DB가 프로세스 모르게 리셋/롤백됐거나, (2) 이 프로젝트의 시험·데모
    하네스가 매 시나리오·매 데모 실행마다 아웃박스 행을 지우는 정상 운영 경로를
    탔다(`tests/integration/harness.py`의 `_purge`/`_purge_abandoned`,
    `packages/orchestrator/cli.py`의 `_purge`). 두 경우 모두 그대로 두면
    `event_id > last_id` 조건에 새 행이 영원히 걸리지 않는다(무증상 유실) — 이
    함수는 그 조건이 깨졌다는 사실 자체(원인은 안 가린다)만 보고 되감는다.
    """
    table_max = await current_max_id(session_maker)
    if last_id > table_max:
        recovered = await resume_position(session_maker)
        logger.info(
            "커서보다 작은 event_id의 행이 테이블에서 사라졌다(last_id=%d > "
            "table_max=%d) — DB 리셋 또는 정상적인 purge로 보고 재개 지점을 "
            "%d로 되감는다",
            last_id, table_max, recovered,
        )
        return recovered
    return last_id


async def mark_published(session_maker, event_ids: list[int]) -> None:
    """전달에 실제로 성공한 행만 발행 표시한다."""
    if not event_ids:
        return
    async with session_maker() as s:
        await s.execute(
            update(OutboxEvent)
            .where(OutboxEvent.event_id.in_(event_ids))
            .values(published_at=datetime.now(timezone.utc))
        )
        await s.commit()


async def deliver(clients: set[SendsJson], event: dict) -> bool:
    """연결된 클라이언트 전체에 팬아웃하고, 한 곳이라도 성공하면 True를 돌려준다.

    전송에 실패한(끊어진) 클라이언트는 즉시 집합에서 뺀다 — 다음 사이클에
    다시 시도되지 않으며, 재연결하면 새 WebSocket 객체로 다시 등록된다.
    """
    delivered = False
    for ws in list(clients):
        try:
            await ws.send_json(event)
            delivered = True
        except Exception:
            clients.discard(ws)
    return delivered


async def pump_once(session_maker, clients: set[SendsJson], last_id: int) -> int:
    """폴 한 사이클: 읽고 → 전달하고 → 성공한 것만 표시한다.

    클라이언트가 하나도 없으면 그대로 아무것도 하지 않고 `last_id`를
    돌려준다 — `tail_outbox`조차 부르지 않으니 아무것도 발행되지 않는다.

    전달이 실패한 이벤트를 만나면 그 지점에서 멈춘다(그 뒤에 읽힌 이벤트도
    커서를 전진시키지 않고 함께 다음 사이클로 넘긴다) — 그러지 않으면 실패한
    행보다 큰 `event_id`로 커서가 앞서가 버려서, 그 실패한 행을
    `event_id > last_id` 조건 밖으로 영영 밀어내 버린다.
    """
    if not clients:
        return last_id
    last_id = await recover_if_stale(session_maker, last_id)
    events = await tail_outbox(session_maker, last_id)
    delivered_ids: list[int] = []
    for event in events:
        if not await deliver(clients, event):
            break
        delivered_ids.append(event["event_id"])
        last_id = event["event_id"]
    await mark_published(session_maker, delivered_ids)
    return last_id
