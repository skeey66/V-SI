"""MCP 도구를 모델에게 노출하고, 모델의 호출을 서버로 중계한다.

**이 모듈이 스펙 §6.1 의 집행 지점이다.** MCP 서버의 도구는 `requirement_id`
인자를 받지만, 모델에게 주는 스키마에서는 그 인자를 지운다. 호출할 때 세션이
자기 값을 주입한다. 모델이 인자를 지어내더라도 세션 값이 덮어쓴다.

허용 목록도 여기서 검사한다. **서버가 역할별 권한의 1차 집행자다** — Task 3
는 역할마다 별도의 MCP 서버 인스턴스를 `/mcp/<role>/` 에 마운트하므로, 허용
되지 않은 도구는 애초에 그 경로에 존재하지 않는다. 여기서의 검사는 2차
방어선이다: 도구를 아예 보여주지 않아 모델이 없는 도구를 부르려 턴을
낭비하지 않게 하고, 클라이언트 스스로도 자기 역할에 정직하도록 한다.

주의: 이 검사는 `__malformed_tool_call__` (Task 4 의 손상된 tool_call
센티널) 도 예외 없이 걸러낸다 — 그 이름은 어떤 허용 목록에도 없으므로
`ToolNotAllowed` 를 받는다. 이는 의도된 동작이다. 이 예외를 모델이 읽을 수
있는 피드백으로 바꾸는 일은 대화 루프 과제의 책임이며, 여기서는 하지 않는다.

**`call()` 은 자기 타입 표기(`-> dict`)를 스스로 지킨다.** 실제 MCP SDK 의
`ClientSession.call_tool()` 은 `CallToolResult` 객체를 돌려주지, dict 를
돌려주지 않는다. 이 변환이 다른 파일(예: 세션 래퍼)에 있으면 계약이 두
군데로 쪼개진다 — 이 타입 표기를 낸 파일이 직접 지키는 것이 맞다.
`services/tool_server/main.py` 의 도구 함수는 항상 `{"ok": ..., "detail":
..., ["exit_code": ...]}` 모양을 돌려주고, FastMCP 는 그것을 그대로
`structured_content` 에 담으므로 있으면 그것을 신뢰한다. 없으면(오래된
클라이언트 호환 경로거나 도구 자체가 크래시해 MCP 프로토콜 오류가 된 경우)
`content` 블록의 텍스트와 `is_error` 로 최선의 dict 를 만든다. 이미 dict 를
돌려주는 테스트 더블은 그대로 통과시킨다.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: 모델에게 절대 노출하지 않는 인자.
_HIDDEN_ARGS = frozenset({"requirement_id"})


class ToolNotAllowed(ValueError):
    """이 역할에 허용되지 않은 도구."""


def _input_schema(tool: dict) -> dict:
    # mcp SDK 의 Tool.model_dump()는 기본적으로(별칭 없이) 스네이크케이스
    # "input_schema" 키를 낸다; 와이어 형식(및 이 모듈의 테스트가 쓰는 형태)은
    # 카멜케이스 "inputSchema" 다. 호출자가 어느 쪽을 넘기든 이해한다.
    if "inputSchema" in tool:
        return tool["inputSchema"] or {}
    return tool.get("input_schema") or {}


def tool_schemas(names: Sequence[str], raw: Sequence[dict]) -> list[dict]:
    by_name = {t["name"]: t for t in raw}
    out = []
    for name in names:
        tool = by_name.get(name)
        if tool is None:
            continue
        schema = _input_schema(tool)
        props = {k: v for k, v in schema.get("properties", {}).items() if k not in _HIDDEN_ARGS}
        required = [r for r in schema.get("required", []) if r not in _HIDDEN_ARGS]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": {"type": "object", "properties": props, "required": required},
                },
            }
        )
    return out


def _content_text(content: Any) -> str:
    """`CallToolResult.content` 블록들에서 사람이 읽을 텍스트를 모은다."""
    if not content:
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            texts.append(str(text))
    return "\n".join(texts)


def _call_result_to_dict(result: Any) -> dict:
    """MCP `CallToolResult` (또는 이미 dict 인 테스트 더블)를 이 브리지가
    약속한 `dict` 로 만든다."""
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    return {
        "ok": not getattr(result, "is_error", False),
        "detail": _content_text(getattr(result, "content", None)),
    }


class ToolBridge:
    def __init__(self, session, requirement_id: str, allowed: Sequence[str]) -> None:
        self._session = session
        self._requirement_id = requirement_id
        self._allowed = frozenset(allowed)

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in self._allowed:
            raise ToolNotAllowed(f"'{name}' is not allowed for this role")
        # 세션 값이 마지막에 들어가 모델이 넣은 값을 덮는다.
        args = {**arguments, "requirement_id": self._requirement_id}
        raw = await self._session.call_tool(name, args)
        return _call_result_to_dict(raw)
