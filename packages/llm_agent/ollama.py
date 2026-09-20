"""Ollama `/api/chat` 클라이언트.

`stream=False` 로 한 번에 받는다. 스트리밍은 UI 에 토큰을 흘릴 때나 필요한데,
여기서 필요한 것은 완성된 `tool_calls` 뿐이다.

`OllamaUnavailable` 을 따로 두는 이유는 모델 로딩 중 503 을 TRANSPORT 로
분류하기 위해서다 (스펙 §10). 새 실패 클래스를 만들지 않고 기존 분류에 얹는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


class OllamaUnavailable(RuntimeError):
    """Ollama 가 응답하지 못한다 — 로딩 중이거나 떠 있지 않다."""


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


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
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
        return ChatReply(content=message.get("content", "") or "", tool_calls=calls)
