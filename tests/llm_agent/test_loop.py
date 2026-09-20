import asyncio
import time

import pytest

from llm_agent.loop import LoopFailed, run_loop
from llm_agent.mcp_client import ToolNotAllowed
from llm_agent.ollama import ChatReply, OllamaUnavailable, ToolCall
from llm_agent.roles import ROLES


class _ScriptedLlm:
    """각본대로 응답한다. 실제 모델 없이 루프를 결정적으로 시험한다."""

    def __init__(self, replies: list[ChatReply]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    async def chat(self, messages, tools):
        self.seen.append(list(messages))
        return self._replies.pop(0) if self._replies else ChatReply(content="끝")


class _RecordingBridge:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {"ok": True, "detail": "ok"}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


async def test_tool_result_is_fed_back_into_the_conversation() -> None:
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="다 했다"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "a.py 에 1 자를 썼다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert bridge.calls[0][0] == "write_file"
    last_messages = llm.seen[-1]
    assert any("a.py 에 1 자를 썼다" in str(m.get("content", "")) for m in last_messages)


async def test_turn_cap_ends_the_task_as_failure() -> None:
    forever = [ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})])] * 50
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=_ScriptedLlm(forever), bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[], max_turns=3,
        )
    assert "턴" in str(exc.value)


async def test_budget_overrun_ends_the_task_as_failure() -> None:
    clock = iter([0.0, 1.0, 700.0, 700.0])
    forever = [ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})])] * 50
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=_ScriptedLlm(forever), bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[],
            now=lambda: next(clock), budget_s=600.0,
        )
    assert "예산" in str(exc.value)


async def test_tool_error_becomes_a_message_not_an_exception() -> None:
    """모델이 오류를 보고 고칠 기회를 가져야 한다 (스펙 §6.1)."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "../x", "content": "x"})]),
        ChatReply(content="알겠다"),
    ])
    bridge = _RecordingBridge({"ok": False, "detail": "워크스페이스를 벗어나는 경로다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert any("벗어나는" in str(m.get("content", "")) for m in llm.seen[-1])


async def test_verifier_verdict_comes_from_exit_code_only() -> None:
    """모델이 PASS 라고 말해도 종료코드 1이면 FAIL 이다 (스펙 §5.3)."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("run_tests", {})]),
        ChatReply(content="전부 통과했다! PASS"),
    ])
    bridge = _RecordingBridge({"ok": False, "detail": "1 failed", "exit_code": 1})
    result = await run_loop(
        role=ROLES["qa"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.payload["exit_code"] == 1
    assert result.payload["verdict"] == "FAIL"


async def test_verifier_that_never_called_its_tool_fails() -> None:
    """근거 없는 판정을 만들지 않는다 (스펙 §5.3)."""
    llm = _ScriptedLlm([ChatReply(content="보아하니 괜찮다")])
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["qa"], llm=llm, bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[],
        )
    assert "도구" in str(exc.value)


async def test_non_verifier_payload_has_no_verdict() -> None:
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="완료"),
    ])
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert "verdict" not in result.payload
    assert result.payload["exit_code"] == 0
    assert result.payload["kind"] == "source_code"


async def test_on_tool_callback_fires_for_each_call() -> None:
    """자문 이벤트 발행 지점 (스펙 §8.1)."""
    seen = []
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="완료"),
    ])
    await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
        on_tool=lambda name, args, result: seen.append((name, result["ok"])),
    )
    assert seen == [("write_file", True)]


# --- 브리핑에 없는 세 가지 요구사항 --------------------------------------


class _StrictBridge:
    """실제 ToolBridge 처럼 허용 목록 밖의 도구를 ToolNotAllowed 로 거부한다."""

    def __init__(self, allowed: set[str], result: dict | None = None) -> None:
        self._allowed = allowed
        self._result = result or {"ok": True, "detail": "ok"}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name not in self._allowed:
            raise ToolNotAllowed(f"'{name}' is not allowed for this role")
        return self._result


async def test_tool_not_allowed_becomes_a_message_not_an_exception() -> None:
    """모델이 허용되지 않은 도구를 부르면 예외가 아니라 메시지로 들어간다."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("run_tests", {})]),
        ChatReply(content="알겠다, 다른 방법을 쓰겠다"),
    ])
    bridge = _StrictBridge({"list_files", "read_file", "write_file"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert any("run_tests" in str(m.get("content", "")) for m in llm.seen[-1])


async def test_malformed_tool_call_becomes_a_message_not_an_exception() -> None:
    """ollama.py 의 손상된 tool_call 센티널도 대화를 죽이지 않는다."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall(
            "__malformed_tool_call__",
            {"error": "arguments string was not valid JSON", "raw": {"function": {"name": "write_file"}}},
        )]),
        ChatReply(content="다시 시도하겠다"),
    ])
    bridge = _StrictBridge({"list_files", "read_file", "write_file"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert any(
        "arguments string was not valid JSON" in str(m.get("content", "")) for m in llm.seen[-1]
    )


async def test_tool_named_like_the_sentinel_with_arbitrary_arguments_does_not_crash() -> None:
    """모델이 직접 그 이름을 부르고 임의의 arguments 를 줘도 .get() 이 안전해야 한다."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("__malformed_tool_call__", {"foo": "bar"})]),
        ChatReply(content="계속한다"),
    ])
    bridge = _StrictBridge({"write_file"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2


class _FlakyThenOkLlm:
    def __init__(self, fail_times: int, reply: ChatReply) -> None:
        self._fail_times = fail_times
        self._reply = reply
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise OllamaUnavailable("loading")
        return self._reply


class _AlwaysDownLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        raise OllamaUnavailable("still loading")


async def test_ollama_unavailable_is_retried_without_consuming_a_turn() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    llm = _FlakyThenOkLlm(2, ChatReply(content="끝"))
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[], sleep=fake_sleep,
    )
    assert result.turns == 1
    assert llm.calls == 3
    assert len(sleeps) == 2


async def test_ollama_permanently_unavailable_fails_without_unbounded_retries() -> None:
    """시도 횟수와 누적 백오프가 유한하게 상한돼 있어, 실제 시간으로도 600초
    예산의 극히 일부만 쓰고 포기한다는 것을 (초를 실제로 기다리지 않고서도)
    확인한다."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    down = _AlwaysDownLlm()
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=down, bridge=_RecordingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[], sleep=fake_sleep,
        )
    assert "Ollama" in str(exc.value)
    assert down.calls <= 6  # 시도 횟수가 유한하게 상한되어 있다
    assert sum(sleeps) < 60.0  # 600초 예산의 10% 안쪽에서 포기한다


async def test_a_hanging_chat_call_is_bounded_by_the_remaining_budget() -> None:
    """chat() 한 번이 오래 걸려도 예산 체크가 그 호출 자체를 끊어야 한다."""

    class _HangingLlm:
        async def chat(self, messages, tools):
            await asyncio.sleep(10)
            return ChatReply(content="너무 늦었다")

    started = time.monotonic()
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=_HangingLlm(), bridge=_RecordingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[], budget_s=0.05,
        )
    elapsed = time.monotonic() - started
    assert "예산" in str(exc.value)
    assert elapsed < 2.0
