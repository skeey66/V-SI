from __future__ import annotations

import httpx

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    TaskStore,
)
from a2a.types import AgentCard
from fastapi import FastAPI

#: 푸시 콜백 HTTP 타임아웃(초). 오케스트레이터의 /push는 즉시 202를 돌려주므로
#: 짧게 잡는다 — 여기서 오래 매달리면 에이전트의 이벤트 소비 루프가 밀린다.
PUSH_TIMEOUT_S = 10.0


def create_agent_app(card: AgentCard, executor: AgentExecutor, task_store: TaskStore) -> FastAPI:
    """우리가 FastAPI 앱을 소유하고 SDK 라우트를 부착한다.

    구현자 주의(브리프 대비 실제 SDK 1.1.2 시그니처 차이):
    - `DefaultRequestHandler`는 `agent_card`가 필수 인자다(브리프 스케치에는 없었음).
    - `create_rest_routes`에는 `agent_card` 인자가 없다(request_handler만 받는다).
    실제 시그니처는 `inspect.signature`로 확인했다.

    푸시 발신(Task 10에서 추가):
    `DefaultRequestHandler`는 `push_config_store`와 `push_sender`를 둘 다 받았을
    때만 푸시 콜백을 보낸다(기본값은 둘 다 None이라 조용히 아무것도 보내지 않는다 —
    `_send_push_notification_if_needed` 참고). Task 8은 헬스/카드만 확인했기 때문에
    이 구멍이 드러나지 않았다. 오케스트레이터의 완료 감지가 푸시에 기대므로 여기서
    둘 다 연결한다.

    설정 저장소는 **인메모리**를 쓴다. DB 저장소(`DatabasePushNotificationConfigStore`)
    는 `DatabaseTaskStore`와 같은 지연 CREATE TABLE 경로를 또 하나 만들어 4기 동시
    기동 시 경합 표면을 넓힌다. 푸시 설정은 send_message 요청과 같은 프로세스에서
    쓰이고 소비되므로 프로세스 밖으로 나갈 이유가 없고, 프로세스가 죽어 설정이
    사라지는 경우는 Task 13의 리컨실리에이션이 덮는다.
    """
    app = FastAPI(title=f"v-si agent: {card.name}")

    push_client = httpx.AsyncClient(timeout=PUSH_TIMEOUT_S)
    push_config_store = InMemoryPushNotificationConfigStore()
    push_sender = BasePushNotificationSender(
        httpx_client=push_client, config_store=push_config_store
    )

    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=card,
        push_config_store=push_config_store,
        push_sender=push_sender,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=card),
        rest_routes=create_rest_routes(request_handler=handler),
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "agent": card.name}

    @app.on_event("shutdown")
    async def _close_push_client() -> None:
        await push_client.aclose()

    return app
