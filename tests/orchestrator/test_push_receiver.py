import httpx
from fastapi import FastAPI

from orchestrator.push_receiver import PUSH_TOKEN_HEADER, create_push_router


def build_app(received: list):
    async def on_notification(body: dict) -> None:
        received.append(body)

    app = FastAPI()
    app.include_router(create_push_router(token="secret-1", on_notification=on_notification))
    return app


async def test_valid_token_is_accepted():
    received: list = []
    transport = httpx.ASGITransport(app=build_app(received))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/push",
            json={"task_id": "t1", "state": "completed"},
            headers={PUSH_TOKEN_HEADER: "secret-1"},
        )
    assert r.status_code == 202
    assert received == [{"task_id": "t1", "state": "completed"}]


async def test_wrong_token_is_rejected():
    received: list = []
    transport = httpx.ASGITransport(app=build_app(received))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/push",
            json={"task_id": "t1"},
            headers={PUSH_TOKEN_HEADER: "wrong"},
        )
    assert r.status_code == 401
    assert received == []


async def test_missing_token_is_rejected():
    received: list = []
    transport = httpx.ASGITransport(app=build_app(received))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/push", json={"task_id": "t1"})
    assert r.status_code == 401
    assert received == []


async def test_callback_body_reaches_handler_intact():
    received: list = []
    transport = httpx.ASGITransport(app=build_app(received))
    body = {
        "task_id": "t2",
        "state": "failed",
        "nested": {"reason": "timeout", "codes": [1, 2, 3]},
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/push", json=body, headers={PUSH_TOKEN_HEADER: "secret-1"})
    assert r.status_code == 202
    assert received == [body]
