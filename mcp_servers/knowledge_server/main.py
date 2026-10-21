"""지식 MCP 서버 — 보안 조치 가이드 RAG.

도구 서버(`tool_server`)와 분리한 이유: 도구 서버는 LLM 이 쓴 코드를 실행하는
곳이라 인터넷이 없는 `internal` 망에 있다. 이 서버는 질의 임베딩을 만들려고
OpenAI 를 불러야 하므로 같은 컨테이너에 둘 수 없다.

MCP 의 세 가지 기능을 모두 쓴다.
- tool `search_security_guide(query, k)` — 의미 검색 (모델·노드가 호출)
- resource `guide://security/{rule_id}` — 규칙 ID 로 원문 직접 조회 (계산 없음)
- prompt `security_fix_request(bandit_output)` — bandit 출력에 맞는 가이드를 붙인
  "이렇게 고쳐라" 요청문을 서버가 조립해 준다. 이 서비스의 그래프는 tool 을 쓰지만,
  Claude Desktop 같은 다른 MCP 클라이언트도 같은 지식을 프롬프트로 바로 가져다 쓸 수 있다.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from lg_agent.settings import load_settings  # noqa: E402  (.env 의 OpenAI 키 별칭 처리)

from knowledge_server.index import GuideIndex, load_docs, openai_embedder  # noqa: E402

load_settings()
CACHE_DIR = Path(os.environ.get("VSI_KNOWLEDGE_CACHE", Path(__file__).parent / ".cache"))
INDEX = GuideIndex(load_docs(), openai_embedder(), cache_dir=CACHE_DIR)

server = MCPServer("vsi-knowledge")


def search_security_guide(query: str, k: int = 3) -> dict[str, Any]:
    """bandit 이 보고한 보안 문제에 대한 조치 가이드를 찾는다. 규칙 ID(B602 등)나 문제 설명을 넣는다."""
    return {"ok": True, "hits": INDEX.search(query, k=max(1, min(k, 5)))}


def guide(rule_id: str) -> str:
    doc = INDEX.get(rule_id)
    return doc.text if doc else f"가이드 없음: {rule_id}"


def security_fix_request(bandit_output: str) -> str:
    """bandit 출력을 받아, 문제별 조치 가이드를 붙인 수정 요청 프롬프트를 만든다."""
    hits = INDEX.search(bandit_output, k=3)
    guides = "\n\n".join(h["text"] for h in hits) or "(맞는 가이드를 찾지 못했다)"
    return (
        "아래 bandit 보안 검사 결과를 고쳐라. 사내 조치 가이드의 방법을 따르고, "
        "인수 테스트가 기대하는 동작은 바꾸지 마라.\n\n"
        f"--- bandit 출력 ---\n{bandit_output}\n\n--- 사내 조치 가이드 ---\n{guides}"
    )


server.add_tool(search_security_guide, name="search_security_guide")
server.resource("guide://security/{rule_id}")(guide)
server.prompt(name="security_fix_request", description="bandit 결과에 맞는 조치 가이드를 붙인 수정 요청문")(security_fix_request)

_ALLOWED = [h.strip() for h in os.environ.get(
    "VSI_KNOWLEDGE_ALLOWED_HOSTS", "knowledge:8000,localhost:8011,127.0.0.1:8011").split(",")]

_sub = server.streamable_http_app(
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(allowed_hosts=_ALLOWED),
)


@asynccontextmanager
async def _lifespan(_app: Starlette):
    async with server.session_manager.run():
        yield


app = Starlette(routes=[Mount("/mcp", app=_sub)], lifespan=_lifespan)
