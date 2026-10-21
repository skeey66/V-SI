"""지식 MCP 서버(`mcp_servers/knowledge_server`) 클라이언트와 검색 질의 만들기."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

#: (질의, k, 묶음) → 검색 결과 `[{"id","title","section","score","source","text"}]`
#: 묶음: security · securecoding · qa · testdesign
Retriever = Callable[[str, int, str], Awaitable[list[dict]]]

#: bandit 출력의 한 줄: `>> Issue: [B602:subprocess_popen_with_shell_equals_true] subprocess call with shell=True ...`
_ISSUE = re.compile(r">> Issue: \[(B\d{3}):[^\]]*\]\s*(.+)")


def bandit_query(detail: str, limit: int = 5) -> str:
    """bandit 출력에서 문제 줄만 뽑아 검색 질의를 만든다. 같은 규칙은 한 번만."""
    seen, lines = set(), []
    for rule, text in _ISSUE.findall(detail):
        if rule not in seen:
            seen.add(rule)
            lines.append(f"{rule} {text.strip()}")
    return "\n".join(lines[:limit]) or detail[-500:]


#: pytest 출력에서 실패 원인을 드러내는 줄 — 예외 이름, `E   assert ...`, 수집 오류
_PYTEST_LINE = re.compile(r"^(E\s+.+|.*(Error|Exception|DID NOT RAISE|ERROR collecting).*)$", re.M)


def pytest_query(detail: str, limit: int = 8) -> str:
    """pytest 출력에서 실패 줄만 뽑아 검색 질의를 만든다. 같은 줄은 한 번만."""
    seen, lines = set(), []
    for m in _PYTEST_LINE.finditer(detail):
        line = m.group(0).strip()[:200]
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines[:limit]) or detail[-500:]


def mcp_retriever(base_url: str, timeout_s: float = 20.0) -> Retriever:
    async def search(query: str, k: int = 3, collection: str = "security") -> list[dict]:
        async with asyncio.timeout(timeout_s):
            async with streamable_http_client(f"{base_url}/mcp/") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "search_guides", {"query": query, "collection": collection, "k": k})
        data = getattr(result, "structured_content", None)
        if not isinstance(data, dict):
            text = "".join(getattr(b, "text", "") for b in (result.content or []))
            data = json.loads(text) if text else {}
        return data.get("hits", [])

    return search
