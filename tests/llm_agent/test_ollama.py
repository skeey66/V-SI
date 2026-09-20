import httpx
import pytest

from llm_agent.ollama import ChatReply, OllamaClient, OllamaUnavailable


def _client(handler) -> OllamaClient:
    transport = httpx.MockTransport(handler)
    return OllamaClient(
        base_url="http://fake:11434",
        model="qwen3:8b",
        http=httpx.AsyncClient(transport=transport),
    )


async def test_parses_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "write_file",
                                      "arguments": {"path": "calc.py", "content": "x"}}}
                    ],
                }
            },
        )

    reply = await _client(handler).chat([{"role": "user", "content": "hi"}], [])
    assert isinstance(reply, ChatReply)
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "write_file"
    assert reply.tool_calls[0].arguments["path"] == "calc.py"


async def test_parses_plain_content_when_no_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "다 했다"}})

    reply = await _client(handler).chat([], [])
    assert reply.tool_calls == []
    assert reply.content == "다 했다"


async def test_sends_model_and_tools_and_disables_streaming() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": ""}})

    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    await _client(handler).chat([{"role": "user", "content": "x"}], tools)
    assert seen["model"] == "qwen3:8b"
    assert seen["stream"] is False
    assert seen["tools"] == tools


async def test_503_becomes_ollama_unavailable() -> None:
    """모델 로딩 중 503 은 TRANSPORT 로 분류돼야 한다 (스펙 §10)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="loading model")

    with pytest.raises(OllamaUnavailable):
        await _client(handler).chat([], [])


async def test_string_arguments_are_parsed_as_json() -> None:
    """일부 응답은 arguments 를 JSON 문자열로 준다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
            ]}},
        )

    reply = await _client(handler).chat([], [])
    assert reply.tool_calls[0].arguments == {"path": "a.py"}


async def test_404_does_not_become_ollama_unavailable() -> None:
    """4xx 는 로컬 재시도로 풀리지 않는 조건이므로 503 과 같은 취급을 받으면 안 된다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).chat([], [])


async def test_unparseable_argument_string_is_malformed_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": "{not json"}}
        ]}})

    reply = await _client(handler).chat([], [])
    assert len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call.name == "__malformed_tool_call__"
    assert isinstance(call.arguments, dict)
    assert "error" in call.arguments


async def test_non_dict_arguments_are_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": ["not", "a", "dict"]}}
        ]}})

    reply = await _client(handler).chat([], [])
    assert reply.tool_calls[0].name == "__malformed_tool_call__"
    assert isinstance(reply.tool_calls[0].arguments, dict)


async def test_tool_calls_container_not_a_list_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {
            "content": "", "tool_calls": {"function": {"name": "x", "arguments": {}}}
        }})

    reply = await _client(handler).chat([], [])
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "__malformed_tool_call__"


async def test_tool_call_entry_not_a_dict_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": ["oops"]}})

    reply = await _client(handler).chat([], [])
    assert reply.tool_calls[0].name == "__malformed_tool_call__"


async def test_one_malformed_call_does_not_block_the_others() -> None:
    """세 개 중 하나가 망가져도 나머지 정상 호출은 그대로 반환된다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": {"path": "a.py"}}},
            {"function": {"name": "read_file", "arguments": "{not json"}},
            {"function": {"name": "list_dir", "arguments": {"path": "."}}},
        ]}})

    reply = await _client(handler).chat([], [])
    assert len(reply.tool_calls) == 3
    assert reply.tool_calls[0].name == "write_file"
    assert reply.tool_calls[1].name == "__malformed_tool_call__"
    assert reply.tool_calls[2].name == "list_dir"
