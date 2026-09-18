from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Header, HTTPException, Request, status

PUSH_TOKEN_HEADER = "X-A2A-Notification-Token"


def create_push_router(
    token: str, on_notification: Callable[[dict], Awaitable[None]]
) -> APIRouter:
    """SDK의 BasePushNotificationSender가 보내는 콜백을 받는 라우터를 만든다.

    토큰은 등록 시 우리가 발급해 push notification config store에 저장한 값이며,
    AgentCard에는 절대 포함되지 않는다. 상수 시간 비교(hmac.compare_digest)로
    검증해 타이밍 공격을 막는다.
    """
    router = APIRouter()

    @router.post("/push", status_code=status.HTTP_202_ACCEPTED)
    async def receive(
        request: Request,
        x_a2a_notification_token: str | None = Header(default=None),
    ) -> dict:
        if x_a2a_notification_token is None or not hmac.compare_digest(
            x_a2a_notification_token, token
        ):
            raise HTTPException(status_code=401, detail="invalid notification token")
        await on_notification(await request.json())
        return {"accepted": True}

    return router
