"""4종 스텁 에이전트 컨테이너가 실제로 기동해 A2A 카드와 헬스체크를 응답하는지 검증한다.

전제: `docker compose up -d --build`로 스택이 떠 있어야 한다(호스트 포트
8001~8004). 카드 경로는 `/.well-known/agent-card.json`이다 — a2a-sdk 1.1.2의
`a2a.utils.constants.AGENT_CARD_WELL_KNOWN_PATH`를 그대로 쓰며,
`create_agent_card_routes`에 다른 `card_url`을 넘기지 않는 한 프리픽스도 붙지
않는다(`packages/agent_runtime/app.py`의 `add_a2a_routes_to_fastapi` 호출부
참고, `app.routes`로 직접 확인함).
"""
from __future__ import annotations

import httpx
import pytest

AGENT_PORTS = {"planner": 8001, "dev": 8002, "qa": 8003, "security": 8004}

CARD_PATH = "/.well-known/agent-card.json"

# 카드 본문에 등장하면 안 되는, 자격증명처럼 보이는 문자열들.
# ("password" 자체는 스킴 설명에 등장하지 않지만 예방적으로 포함해 둔다.)
_SECRET_SHAPED = ("token", "bearer ", "sk-", "password=", "vsi:vsi", "-----begin")


@pytest.mark.parametrize("agent,port", AGENT_PORTS.items())
async def test_agent_health_and_card(agent: str, port: int) -> None:
    async with httpx.AsyncClient(base_url=f"http://localhost:{port}", timeout=10) as c:
        health = await c.get("/healthz")
        assert health.status_code == 200
        assert health.json()["agent"] == agent

        card = await c.get(CARD_PATH)
        assert card.status_code == 200
        body = card.json()
        assert body["name"] == agent

        lowered = card.text.lower()
        for shaped in _SECRET_SHAPED:
            assert shaped not in lowered, f"카드에 자격증명처럼 보이는 문자열 발견: {shaped!r}"
