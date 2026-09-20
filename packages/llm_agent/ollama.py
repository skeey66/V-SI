"""Ollama `/api/chat` 클라이언트.

`stream=False` 로 한 번에 받는다. 스트리밍은 UI 에 토큰을 흘릴 때나 필요한데,
여기서 필요한 것은 완성된 `tool_calls` 뿐이다.

`OllamaUnavailable` 은 호출자가 로컬에서 재시도해도 되는 상태(예: 모델이 아직
로딩 중이라 503 을 돌려주는 경우)를 표시하는 마커일 뿐이다. 이 클라이언트
자체는 재시도하지 않는다 — 재시도는 대화 루프 과제에서 붙는다. (참고: 이
예외는 오케스트레이터의 TRANSPORT 분류와는 무관하다. 예외는 A2A 와이어를
건너지 않고, 에이전트를 벗어난 실패는 `stuck_after_s` 를 다 채운 뒤
EXECUTION 으로 강제 종료될 뿐이다.)

**모델이 만드는 tool_calls 는 신뢰하지 않는다.** 8B 모델은 다음과 같은 흔한
방식으로 tool_calls 를 망가뜨릴 수 있다:
  - `arguments` 가 JSON 파싱에 실패하는 문자열이다.
  - `arguments` 가 파싱은 되지만 dict 가 아니다 (리스트, 숫자 등).
  - `tool_calls` 자체가 리스트가 아니다 (dict, 문자열 등).
  - `tool_calls` 의 원소가 dict 가 아니거나 `function` 객체나 `name` 이 없다.

이런 경우 예외를 올려서 `chat()` 전체를 죽이지 않는다. 대신 그 항목만
`name=_MALFORMED_TOOL_NAME` 인 `ToolCall` 로 바꿔치기하고 원인을
`arguments["error"]`/`arguments["raw"]` 에 담아 반환한다 — 루프는 이 이름을
모르는 도구로 취급해 거부하고, 필요하면 그 사유를 모델에게 되돌려줄 수 있다.
**한 항목이 망가져도 같은 응답의 다른 정상 tool_calls 는 그대로 파싱되어
반환된다** — 여러 개 중 하나의 결함이 나머지 정상 호출까지 막지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

_MALFORMED_TOOL_NAME = "__malformed_tool_call__"


class OllamaUnavailable(RuntimeError):
    """Ollama 가 응답하지 못한다 — 로딩 중이거나 떠 있지 않다.

    호출자가 로컬에서(짧은 백오프로) 재시도해도 되는 상태임을 나타내는
    마커일 뿐, 그 자체로 재시도를 수행하지는 않는다.
    """


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def _malformed(reason: str, raw: Any) -> ToolCall:
    return ToolCall(name=_MALFORMED_TOOL_NAME, arguments={"error": reason, "raw": raw})


def _parse_one_tool_call(raw: Any) -> ToolCall:
    if not isinstance(raw, dict):
        return _malformed("tool call entry was not a JSON object", raw)

    fn = raw.get("function")
    if not isinstance(fn, dict):
        return _malformed("tool call missing a 'function' object", raw)

    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return _malformed("tool call function had no usable name", raw)

    args = fn.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return _malformed(f"arguments string was not valid JSON: {exc}", raw)

    if not isinstance(args, dict):
        return _malformed("arguments did not decode to a JSON object", raw)

    return ToolCall(name=name, arguments=args)


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        return [_malformed("tool_calls was not a list", raw_calls)]
    return [_parse_one_tool_call(raw) for raw in raw_calls]


class OllamaClient:
    def __init__(self, base_url: str, model: str, http: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._http = http

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply:
        body = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            resp = await self._http.post(f"{self._base_url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            raise OllamaUnavailable(f"{resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()

        message = resp.json().get("message", {})
        calls = _parse_tool_calls(message.get("tool_calls"))
        return ChatReply(content=message.get("content", "") or "", tool_calls=calls)
