"""MCP 도구를 LangChain 도구로 감싼다.

**공식 `langchain-mcp-adapters` 를 쓰지 않는 이유.** 이 프로젝트는 `mcp==2.2.0`
을 쓰는데, 어댑터 최신판(0.3.1)은 설치는 되지만 import 하는 순간
`ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'`
로 죽는다(mcp 2.x 에서 옮겨진 이름). 도구 서버를 1.x 로 내리면 이미 검증된
서버 코드를 고쳐야 하므로, 클라이언트 쪽에 얇은 어댑터를 둔다.

연결·스키마 정리·인자 주입은 `lg_agent.mcp_bridge`(이전 버전에서 가져온 코드)를 쓴다:
- `tool_schemas` 가 모델에게 보여줄 스키마에서 `requirement_id` 를 지운다.
- `ToolBridge` 가 호출 시 `requirement_id` 를 세션 값으로 덮어쓰고 허용 목록을
  검사한다. 모델은 다른 요구사항의 워크스페이스를 가리킬 방법이 없다.

도구 오류는 예외가 아니라 `{"ok": False, ...}` 결과로 모델에게 돌아간다
(`legacy/packages/llm_agent/loop.py` 와 같은 원칙) — 모델이 보고 고칠 기회를 갖는다.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lg_agent.events import emit
from lg_agent.mcp_bridge import ToolBridge, ToolNotAllowed, tool_schemas
from lg_agent.roles import ROLES

MCP_CONNECT_TIMEOUT_S = 30.0
#: 도구 호출 하나의 상한. 도구 서버의 서브프로세스 상한(60초)에 왕복 여유를 더했다
#: (`legacy/packages/llm_agent/loop.py` 의 `TOOL_CALL_CAP_S` 와 같은 근거).
TOOL_CALL_CAP_S = 90.0


@dataclass
class RoleTools:
    role: str
    bridge: ToolBridge
    tools: list[BaseTool]

    async def call(self, name: str, arguments: dict | None = None) -> dict:
        """모델을 거치지 않고 도구를 직접 부른다 — 판정 도구(`run_tests` 등)용."""
        return await _safe_call(self.role, self.bridge, name, arguments or {})


async def _safe_call(role: str, bridge: ToolBridge, name: str, arguments: dict) -> dict:
    emit({"type": "tool_call", "agent": role, "tool": name, "args": arguments})
    try:
        result = await asyncio.wait_for(bridge.call(name, arguments), timeout=TOOL_CALL_CAP_S)
    except ToolNotAllowed as exc:
        result = {"ok": False, "detail": str(exc)}
    except TimeoutError:
        result = {"ok": False, "detail": f"'{name}' 호출이 {TOOL_CALL_CAP_S}초 안에 끝나지 않았다"}
    except Exception as exc:  # 끊긴 세션·네트워크 오류도 모델이 읽을 메시지로
        result = {"ok": False, "detail": f"'{name}' 호출이 예외로 끝났다: {exc}"}
    emit({
        "type": "tool_result", "agent": role, "tool": name,
        "ok": result.get("ok"), "exit_code": result.get("exit_code"),
    })
    return result


def _wrap(role: str, bridge: ToolBridge, schema: dict) -> BaseTool:
    fn = schema["function"]
    name = fn["name"]

    async def _run(**kwargs: Any) -> str:
        result = await _safe_call(role, bridge, name, kwargs)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name=name,
        description=fn.get("description") or name,
        args_schema=fn["parameters"],
        coroutine=_run,
    )


@asynccontextmanager
async def role_tools(base_url: str, role: str, requirement_id: str) -> AsyncIterator[RoleTools]:
    """역할 경로(`/mcp/<role>/`)에 연결해 그 역할의 도구만 LangChain 도구로 낸다."""
    allowed = ROLES[role].tools
    url = f"{base_url}/mcp/{role}/"
    async with AsyncExitStack() as stack:
        async with asyncio.timeout(MCP_CONNECT_TIMEOUT_S):
            read, write = await stack.enter_async_context(streamable_http_client(url))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
        raw = [
            {"name": t.name, "description": t.description or "",
             "input_schema": t.input_schema or {}}
            for t in listed.tools
        ]
        schemas = tool_schemas(allowed, raw)
        missing = set(allowed) - {s["function"]["name"] for s in schemas}
        if missing:
            raise RuntimeError(f"{url} 가 '{role}' 역할 도구를 다 광고하지 않는다: {sorted(missing)}")
        bridge = ToolBridge(session, requirement_id, allowed)
        yield RoleTools(role=role, bridge=bridge, tools=[_wrap(role, bridge, s) for s in schemas])
