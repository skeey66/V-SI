"""지식 MCP 서버(`mcp_servers/knowledge_server`) 클라이언트 — 보안 조치 가이드 검색."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

#: (질의, k) → 검색 결과 `[{"id","title","score","text"}]`
Retriever = Callable[[str, int], Awaitable[list[dict]]]

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


def mcp_retriever(base_url: str, timeout_s: float = 20.0) -> Retriever:
    async def search(query: str, k: int = 3) -> list[dict]:
        async with asyncio.timeout(timeout_s):
            async with streamable_http_client(f"{base_url}/mcp/") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("search_security_guide", {"query": query, "k": k})
        data = getattr(result, "structured_content", None)
        if not isinstance(data, dict):
            text = "".join(getattr(b, "text", "") for b in (result.content or []))
            data = json.loads(text) if text else {}
        return data.get("hits", [])

    return search
