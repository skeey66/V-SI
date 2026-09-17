from __future__ import annotations

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import TaskStore
from a2a.types import AgentCard
from fastapi import FastAPI


def create_agent_app(card: AgentCard, executor: AgentExecutor, task_store: TaskStore) -> FastAPI:
    """우리가 FastAPI 앱을 소유하고 SDK 라우트를 부착한다.

    구현자 주의(브리프 대비 실제 SDK 1.1.2 시그니처 차이):
    - `DefaultRequestHandler`는 `agent_card`가 필수 인자다(브리프 스케치에는 없었음).
    - `create_rest_routes`에는 `agent_card` 인자가 없다(request_handler만 받는다).
    실제 시그니처는 `inspect.signature`로 확인했다.
    """
    app = FastAPI(title=f"v-si agent: {card.name}")
    handler = DefaultRequestHandler(agent_executor=executor, task_store=task_store, agent_card=card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=card),
        rest_routes=create_rest_routes(request_handler=handler),
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "agent": card.name}

    return app
