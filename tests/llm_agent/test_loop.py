import asyncio
import time

import pytest

from llm_agent.loop import MAX_VERDICT_NUDGES, LoopFailed, run_loop
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
    """근거 없는 판정을 만들지 않는다 (스펙 §5.3). 정정 기회(nudge)를 다
    써버린 뒤에도 여전히 도구를 안 부르면 실패로 끝난다."""
    llm = _ScriptedLlm([ChatReply(content="보아하니 괜찮다")] * (MAX_VERDICT_NUDGES + 5))
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["qa"], llm=llm, bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[],
        )
    assert "도구" in str(exc.value)
    # nudge 는 유한하다 — 원래 시도 1번 + nudge 횟수만큼만 chat() 이 불린다.
    assert len(llm.seen) == MAX_VERDICT_NUDGES + 1


async def test_verifier_gets_a_nudge_and_recovers_on_the_next_turn() -> None:
    """검증자가 판정 도구를 안 부르고 턴을 끝내면, 예외 대신 계약을 알리는
    메시지가 대화에 들어가고 모델은 다음 턴을 받는다."""
    llm = _ScriptedLlm([
        ChatReply(content="보아하니 통과다"),
        ChatReply(tool_calls=[ToolCall("run_security_scan", {})]),
        ChatReply(content="끝"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "이상 없음", "exit_code": 0})
    result = await run_loop(
        role=ROLES["security"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.payload["verdict"] == "PASS"
    assert bridge.calls[0][0] == "run_security_scan"
    # 첫 응답(도구 미호출) 다음에 오간 메시지에 정정 계약이 들어 있어야 한다.
    second_call_messages = llm.seen[1]
    assert any(
        m.get("role") == "assistant" and m.get("content") == "보아하니 통과다"
        for m in second_call_messages
    )
    assert any(
        m.get("role") == "user" and "run_security_scan" in str(m.get("content", ""))
        for m in second_call_messages
    )


async def test_verdict_nudge_states_the_contract_not_a_plea() -> None:
    """정정 메시지는 애원이 아니라 계약을 진술해야 한다 — 도구 이름과 '판정은
    도구에서만 나온다'는 사실이 그대로 들어 있는지를 확인한다."""
    llm = _ScriptedLlm([
        ChatReply(content="괜찮아 보인다"),
        ChatReply(tool_calls=[ToolCall("run_tests", {})]),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "ok", "exit_code": 0})
    await run_loop(
        role=ROLES["qa"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    nudge = next(
        m for m in llm.seen[1]
        if m.get("role") == "user" and "run_tests" in str(m.get("content", ""))
    )
    assert "run_tests" in nudge["content"]


async def test_verdict_nudge_consumes_a_turn() -> None:
    """nudge 도 실제로 모델을 한 번 부른 턴이다 (모델이 아예 안 돈 Ollama
    재시도와 다르다) — `turns` 에 그대로 반영돼야 한다."""
    llm = _ScriptedLlm([
        ChatReply(content="괜찮아 보인다"),
        ChatReply(tool_calls=[ToolCall("run_tests", {})]),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "ok", "exit_code": 0})
    result = await run_loop(
        role=ROLES["qa"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    # 1) nudge 유발 턴, 2) run_tests 를 부른 턴, 3) 도구 결과를 보고 끝내는 턴.
    assert result.turns == 3


async def test_verdict_nudge_respects_the_turn_cap() -> None:
    """nudge 가 `max_turns` 상한 자체를 우회하면 안 된다."""
    llm = _ScriptedLlm([ChatReply(content="괜찮다")] * 5)
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["qa"], llm=llm, bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[], max_turns=1,
        )
    assert "턴" in str(exc.value)


async def test_non_verifier_that_calls_no_tools_fails_without_a_nudge() -> None:
    """dev/planner 가 도구를 하나도 안 부르고 턴을 끝내면 그 자체로 EXECUTION
    실패다 (스펙 §5.3, §10 실패표 — "도구 호출 0회"는 무조건 EXECUTION
    실패다). 다만 검증자 전용 nudge 경로는 타지 않는다 — 정정 대상은
    "판정 도구를 안 불렀다"는 검증자 특유의 계약 위반이지, 비검증자가 그냥
    아무것도 안 한 것을 재촉해서 고칠 성질이 아니다. 그래서 재시도 없이
    첫 턴 만에 `LoopFailed` 로 끝나야 한다.

    (이전 버전의 이 시험은 정반대 — `result.turns == 1`로 "정상 종료"를
    단언했다. 그게 바로 이 수정이 없애는 버그다: 기획이 프리텍스트만 남기고
    아무 파일도 안 써도 `exit_code=0`짜리 "성공" 산출물이 기록됐다.)"""
    llm = _ScriptedLlm([ChatReply(content="설계만 하고 끝냈다")])
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["planner"], llm=llm, bridge=_RecordingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[],
        )
    assert "도구" in str(exc.value)
    assert len(llm.seen) == 1  # nudge 없이 첫 턴 만에 실패 확정.


async def test_dev_that_calls_no_tools_fails() -> None:
    """같은 실패가 dev 에도 적용된다 — planner 전용 버그가 아니다."""
    llm = _ScriptedLlm([ChatReply(content="음, 어렵네")])
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[],
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
    """자문 이벤트 발행 지점 (스펙 §8.1). `on_tool` 은 awaitable 계약이다 —
    `record_tool_event` 처럼 DB에 쓰는 코루틴 함수가 실제 호출자다."""
    seen = []

    async def on_tool(name, args, result):
        seen.append((name, result["ok"]))

    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="완료"),
    ])
    await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
        on_tool=on_tool,
    )
    assert seen == [("write_file", True)]


async def test_on_tool_callback_is_actually_awaited() -> None:
    """회귀 방지: `on_tool` 이 코루틴 함수인데 `await` 없이 호출되면, 코루틴은
    게으르므로 함수 본문이 단 한 줄도 안 돈다 — 예외도 안 나고 조용히
    아무 일도 없었던 것처럼 지나간다. 여기서 그 증거(호출 횟수)로 잡는다."""
    calls = []

    async def on_tool(name, args, result):
        calls.append(1)

    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="완료"),
    ])
    await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
        on_tool=on_tool,
    )
    assert len(calls) == 1


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
    def __init__(self, fail_times: int, replies: list[ChatReply]) -> None:
        self._fail_times = fail_times
        self._replies = list(replies)
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise OllamaUnavailable("loading")
        return self._replies.pop(0) if self._replies else ChatReply(content="끝")


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

    # 도구 호출이 하나도 없으면(Critical 2) 이 테스트가 확인하려는 것과
    # 무관한 이유로 `LoopFailed`가 난다 — 재시도 회계만 보고 싶으므로 회복
    # 직후 응답에 도구 호출을 하나 담고, 그다음 턴에 도구 없이 끝낸다.
    llm = _FlakyThenOkLlm(2, [
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="끝"),
    ])
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[], sleep=fake_sleep,
    )
    assert result.turns == 2
    assert llm.calls == 4
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


# --- 리뷰 라운드 1 수정 사항 ------------------------------------------------


class _AdvancingClock:
    """`bridge.call` 이 걸릴 때마다 논리 시계를 앞으로 돌린다."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def advance(self, delta: float) -> None:
        self.value += delta


class _SlowBridge:
    def __init__(self, clock: _AdvancingClock, per_call_s: float) -> None:
        self._clock = clock
        self._per_call = per_call_s
        self.calls = 0

    async def call(self, name, arguments):
        self.calls += 1
        self._clock.advance(self._per_call)
        return {"ok": True, "detail": "ok"}


async def test_budget_is_checked_between_tool_calls_in_a_single_reply() -> None:
    """네 개의 도구 호출이 든 응답 하나가 예산을 몇 배로 넘기지 못해야 한다."""
    clock = _AdvancingClock()
    bridge = _SlowBridge(clock, per_call_s=500.0)
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[
            ToolCall("write_file", {"path": "a.py", "content": "x"}),
            ToolCall("write_file", {"path": "b.py", "content": "x"}),
            ToolCall("write_file", {"path": "c.py", "content": "x"}),
            ToolCall("write_file", {"path": "d.py", "content": "x"}),
        ]),
    ])
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
            title="계산기", revision=1, feedback=[],
            now=clock.now, budget_s=600.0,
        )
    assert "예산" in str(exc.value)
    # 예산을 이미 넘긴 다음 호출은 아예 시도되지 않는다 — 4개 전부 도는 것도,
    # 다음 while 반복까지 기다리는 것도 아니다.
    assert bridge.calls < 4


async def test_a_hanging_tool_call_is_bounded_by_the_remaining_budget() -> None:
    """멈춘 도구 호출 하나가 chat() 처럼 남은 예산으로 잘려야 한다."""

    class _HangingBridge:
        async def call(self, name, arguments):
            await asyncio.sleep(10)
            return {"ok": True, "detail": "너무 늦었다"}

    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
    ])
    started = time.monotonic()
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=llm, bridge=_HangingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[], budget_s=0.05,
        )
    elapsed = time.monotonic() - started
    assert "예산" in str(exc.value)
    assert elapsed < 2.0


async def test_bridge_exception_becomes_a_message_not_an_exception() -> None:
    """`ToolNotAllowed` 말고 다른 예외(끊긴 세션 등)도 대화를 죽이면 안 된다."""

    class _FlakyBridge:
        async def call(self, name, arguments):
            raise RuntimeError("MCP 세션이 끊겼다")

    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="알겠다"),
    ])
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_FlakyBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert any("세션이 끊겼다" in str(m.get("content", "")) for m in llm.seen[-1])


async def test_non_dict_tool_result_from_a_verifier_does_not_crash_the_loop() -> None:
    """dict 가 아닌 결과에 `.get("exit_code")` 를 부르면 AttributeError 다 —
    근거 없는 통과를 만들지 않되, 예외로 죽지도 않아야 한다."""

    class _WeirdBridge:
        async def call(self, name, arguments):
            return "그냥 문자열"

    llm = _ScriptedLlm([ChatReply(tool_calls=[ToolCall("run_tests", {})])])
    with pytest.raises(LoopFailed):
        await run_loop(
            role=ROLES["qa"], llm=llm, bridge=_WeirdBridge(), schemas=[],
            title="계산기", revision=1, feedback=[],
        )


async def test_unserializable_tool_result_does_not_crash_the_loop() -> None:
    """`json.dumps` 가 그대로 못 삼키는 값이 결과에 섞여도 죽지 않는다."""

    class _NotJsonNative:
        def __repr__(self) -> str:
            return "<특이한 객체>"

    class _WeirdBridge:
        async def call(self, name, arguments):
            return {"ok": True, "detail": "ok", "payload": _NotJsonNative()}

    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="끝"),
    ])
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_WeirdBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2


async def test_ollama_http_status_error_becomes_loopfailed_without_retry() -> None:
    """404(모델을 안 받아온 흔한 설정 실수) 는 재시도 대상이 아니다."""
    import httpx

    class _Http404Llm:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools):
            self.calls += 1
            request = httpx.Request("POST", "http://fake/api/chat")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

    llm = _Http404Llm()
    with pytest.raises(LoopFailed):
        await run_loop(
            role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[],
        )
    assert llm.calls == 1


async def test_ollama_json_decode_error_becomes_loopfailed() -> None:
    import json as _json

    class _BadJsonLlm:
        async def chat(self, messages, tools):
            raise _json.JSONDecodeError("bad", "doc", 0)

    with pytest.raises(LoopFailed):
        await run_loop(
            role=ROLES["dev"], llm=_BadJsonLlm(), bridge=_RecordingBridge(), schemas=[],
            title="계산기", revision=1, feedback=[],
        )


async def test_a_hanging_tool_call_is_capped_independent_of_the_remaining_budget() -> None:
    """멈춘 도구 호출 하나가 남은 예산(600초) 전체를 태우면 안 된다 —
    남은 예산과 무관한 짧은 상한으로 잘려, 모델이 실제로 다음 턴을 받는다."""

    class _HangingBridge:
        async def call(self, name, arguments):
            await asyncio.sleep(5.0)
            return {"ok": True, "detail": "너무 늦었다"}

    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="다른 방법을 쓰겠다"),
    ])
    started = time.monotonic()
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_HangingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
        budget_s=600.0, tool_call_cap_s=0.05,
    )
    elapsed = time.monotonic() - started
    assert result.turns == 2
    assert elapsed < 1.0
    # 잘렸다는 사실 자체가 모델에게 메시지로 전달돼야 한다 — 그냥 빈 결과나
    # 예외로 사라지면 상한을 둔 의미가 없다.
    assert any("끝나지 않았다" in str(m.get("content", "")) for m in llm.seen[-1])


# ---------------------------------------------------------------------------
# Critical 1: 산출물 payload 에 실제로 작성된 파일 내용을 담는다 (스펙 §6.3/§5.2)
# ---------------------------------------------------------------------------


async def test_dev_payload_captures_written_files_from_the_call_arguments() -> None:
    """`engine.on_task_completed` 가 DB 에 담을 유일한 근거는 이 payload 다
    (스펙 §6.3) — write_file 호출 인자에서 path/content 를 그대로 뽑아
    `files` 에 넣는다. 워크스페이스를 되읽는 게 아니라 **모델이 실제로 부른
    호출의 인자**가 출처다."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[
            ToolCall("write_file", {"path": "a.py", "content": "def add(a, b):\n    return a + b\n"}),
            ToolCall("write_file", {"path": "b.py", "content": "x = 1\n"}),
        ]),
        ChatReply(content="완료"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "썼다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.payload["files"] == {
        "a.py": "def add(a, b):\n    return a + b\n",
        "b.py": "x = 1\n",
    }


async def test_a_later_write_to_the_same_path_replaces_the_earlier_one() -> None:
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "one"})]),
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "two"})]),
        ChatReply(content="완료"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "썼다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.payload["files"] == {"a.py": "two"}


async def test_a_rejected_write_does_not_appear_in_the_files_snapshot() -> None:
    """경로 탈출 시도처럼 `ok: False`로 끝난 쓰기는 산출물이 아니다 — 실행이
    실패했는데도 파일이 "생산된 것처럼" 기록되면 §6.3 의 근거가 거짓이 된다."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "../escape.py", "content": "evil"})]),
        ChatReply(content="포기"),
    ])
    bridge = _RecordingBridge({"ok": False, "detail": "워크스페이스를 벗어나는 경로다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.payload["files"] == {}


async def test_verifiers_have_no_files_key_in_the_payload() -> None:
    """qa/security 는 write_file 도구 자체가 없다(권한 분리, 스펙 §5.1) —
    항상 빈 `files`를 넣느니, 이 산출물 종류엔 애초에 해당 개념이 없다는
    뜻으로 키 자체를 뺀다."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("run_tests", {})]),
        ChatReply(content="끝"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "이상 없음", "exit_code": 0})
    result = await run_loop(
        role=ROLES["qa"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert "files" not in result.payload


async def test_a_file_over_the_per_file_cap_is_truncated_and_flagged() -> None:
    from llm_agent.loop import FILE_SNAPSHOT_PER_FILE_CAP_BYTES

    huge = "x" * (FILE_SNAPSHOT_PER_FILE_CAP_BYTES + 500)
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "big.py", "content": huge})]),
        ChatReply(content="완료"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "썼다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    stored = result.payload["files"]["big.py"]
    assert len(stored.encode("utf-8")) <= FILE_SNAPSHOT_PER_FILE_CAP_BYTES + 200
    assert len(stored) < len(huge)
    assert result.payload["files_truncated"] is True


async def test_total_snapshot_cap_is_enforced_across_many_files() -> None:
    from llm_agent.loop import FILE_SNAPSHOT_TOTAL_CAP_BYTES

    # 파일당 상한보다는 한참 작지만, 개수를 늘리면 합이 전체 상한을 넘는다.
    chunk = "y" * 1_000
    n = (FILE_SNAPSHOT_TOTAL_CAP_BYTES // len(chunk)) + 5
    calls = [ToolCall("write_file", {"path": f"f{i}.py", "content": chunk}) for i in range(n)]
    llm = _ScriptedLlm([ChatReply(tool_calls=calls), ChatReply(content="완료")])
    bridge = _RecordingBridge({"ok": True, "detail": "썼다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    total = sum(len(v.encode("utf-8")) for v in result.payload["files"].values())
    assert total <= FILE_SNAPSHOT_TOTAL_CAP_BYTES + 200
    assert result.payload["files_truncated"] is True
