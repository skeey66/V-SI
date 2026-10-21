"""지식 MCP 서버 — 네 가지 지식 묶음의 RAG.

도구 서버(`tool_server`)와 분리한 이유: 도구 서버는 LLM 이 쓴 코드를 실행하는
곳이라 인터넷이 없는 `internal` 망에 있다. 이 서버는 질의 임베딩을 만들려고
OpenAI 를 불러야 하므로 같은 컨테이너에 둘 수 없다.

묶음(`index.py` 참고): security · securecoding · qa · testdesign

MCP 의 세 가지 기능을 모두 쓴다.
- tool `search_guides(query, collection, k)` — 묶음 하나에서 의미 검색 (그래프 노드가 호출)
  `search_security_guide(query, k)` 는 security 묶음 전용 별칭(이전 버전 호환)
- resource `guide://{collection}/{doc_id}` — 문서 원문 직접 조회 (계산 없음)
- prompt `security_fix_request(bandit_output)` · `test_design_brief(requirement)` —
  검색 결과를 붙인 요청문을 서버가 조립한다. 이 서비스의 그래프는 tool 을 쓰지만,
  Claude Desktop 같은 다른 MCP 클라이언트도 같은 지식을 프롬프트로 바로 쓸 수 있다.
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

from knowledge_server.index import COLLECTIONS, build_indexes, openai_embedder  # noqa: E402

load_settings()
CACHE_DIR = Path(os.environ.get("VSI_KNOWLEDGE_CACHE", Path(__file__).parent / ".cache"))
INDEXES = build_indexes(openai_embedder(), cache_dir=CACHE_DIR)

server = MCPServer("vsi-knowledge")


def search_guides(query: str, collection: str = "security", k: int = 3) -> dict[str, Any]:
    """지식 묶음 하나에서 관련 문서를 찾는다.

    collection: security(보안 조치) · securecoding(시큐어 코딩) · qa(테스트 실패 진단) ·
    testdesign(테스트 설계). 결과마다 출처(source)가 붙는다.
    """
    index = INDEXES.get(collection)
    if index is None:
        return {"ok": False, "detail": f"알 수 없는 묶음: {collection} (가능: {', '.join(COLLECTIONS)})"}
    return {"ok": True, "collection": collection, "hits": index.search(query, k=max(1, min(k, 5)))}


def search_security_guide(query: str, k: int = 3) -> dict[str, Any]:
    """bandit 이 보고한 보안 문제에 대한 조치 가이드를 찾는다 (security 묶음 별칭)."""
    return search_guides(query, "security", k)


def guide(collection: str, doc_id: str) -> str:
    index = INDEXES.get(collection)
    doc = index.get(doc_id) if index else None
    return f"# {doc.title}\n출처: {doc.source}\n\n{doc.text}" if doc else f"문서 없음: {collection}/{doc_id}"


def _joined(hits: list[dict]) -> str:
    return "\n\n".join(f"[{h['title']} › {h['section']}] ({h['source']})\n{h['text']}" for h in hits)


def security_fix_request(bandit_output: str) -> str:
    """bandit 출력을 받아, 문제별 조치 가이드를 붙인 수정 요청 프롬프트를 만든다."""
    guides = _joined(INDEXES["security"].search(bandit_output, k=3)) or "(맞는 가이드를 찾지 못했다)"
    return (
        "아래 bandit 보안 검사 결과를 고쳐라. 조치 가이드의 방법을 따르고, "
        "인수 테스트가 기대하는 동작은 바꾸지 마라.\n\n"
        f"--- bandit 출력 ---\n{bandit_output}\n\n--- 조치 가이드 ---\n{guides}"
    )


def test_design_brief(requirement: str) -> str:
    """요구사항에 맞는 테스트 설계 체크리스트를 붙인 인수 테스트 작성 요청문."""
    guides = _joined(INDEXES["testdesign"].search(requirement, k=3)) or "(맞는 체크리스트가 없다)"
    return (
        "아래 요구사항의 pytest 인수 테스트를 써라. 체크리스트의 경계값·예외 케이스를 빠짐없이 "
        f"반영해라.\n\n--- 요구사항 ---\n{requirement}\n\n--- 테스트 설계 체크리스트 ---\n{guides}"
    )


server.add_tool(search_guides, name="search_guides")
server.add_tool(search_security_guide, name="search_security_guide")
server.resource("guide://{collection}/{doc_id}")(guide)
server.prompt(name="security_fix_request", description="bandit 결과에 맞는 조치 가이드를 붙인 수정 요청문")(security_fix_request)
server.prompt(name="test_design_brief", description="요구사항에 맞는 테스트 설계 체크리스트를 붙인 테스트 작성 요청문")(test_design_brief)

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
