"""LLM 대화 루프.

**안쪽 상한은 행동할 수 있는 쪽이 집행한다** (스펙 §9.2). 에이전트는 자기가 턴
상한이나 시간 예산을 넘긴 것을 알고 스스로 실패로 끝낸다. 그래서 리컨실러의
`stuck_after_s` 는 "느린 작업"이 아니라 "죽은 프로세스"만 잡는 역할로 돌아간다.

도구 오류는 예외가 아니라 대화 메시지로 들어간다 (스펙 §6.1). 모델이 보고
고칠 기회를 갖는다. 이는 도구 *실행*이 `{"ok": False, ...}` 를 돌려주는
경우뿐 아니라, `bridge.call` 이 `ToolNotAllowed` 를 던지는 경우에도 똑같이
적용된다 — 모델이 자기 역할에 없는 도구를 부르거나(흔한 8B 모델의 실수),
`ollama.py` 가 파싱하지 못한 tool_call 을 `__malformed_tool_call__` 센티널로
바꿔치기했을 때(그 이름은 어떤 허용 목록에도 없다) 모두 여기서 잡아 메시지로
되돌린다. 이 파일에는 그 예외가 대화 루프를 벗어나 죽이는 경로가 없다.

`OllamaUnavailable` (대개 모델이 아직 로딩 중이라 겪는, 로컬에서 재시도하면
풀리는 상태)는 이 루프 안에서 백오프를 두고 재시도한다 — 오케스트레이터
쪽으로는 넘어가지 않는다(예외는 A2A 와이어를 건너지 않고, 넘어간 실패는
`stuck_after_s` 를 다 채운 뒤에야 EXECUTION 으로 강제 종료될 뿐이라 그 경로는
"느린 시작"과 "죽은 프로세스"를 구분하지 못한다). 재시도는 턴을 소모하지
않는다 — 로딩 중인 모델은 아직 턴을 쓰지 않았다.

`chat()` 호출 하나가 워밍업 상태에서도 12~15초, 동시 부하 아래서는 훨씬 더
걸릴 수 있다. 매 반복 시작 시점의 예산 검사만으로는 그 호출이 도는 동안
예산을 크게 넘겨버릴 수 있으므로, 호출 자체를 **남은 예산**으로 감싸
(`asyncio.wait_for`) 예산을 사실상 집행 지점으로 만든다. 그래서 이 루프는
오케스트레이터의 900초 천장(죽은 프로세스만 잡기 위한 것)보다 먼저, 신뢰성
있게 끝난다.

검증자의 `verdict` 는 도구 종료코드에서만 온다 (스펙 §5.3). 모델 응답 텍스트는
`summary` 로만 들어가고 판정에 영향을 주지 않는다 — 이 파일에 그 경로가 없다.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from llm_agent.mcp_client import ToolNotAllowed
from llm_agent.ollama import OllamaUnavailable
from llm_agent.roles import Role, system_prompt, task_prompt

MAX_TURNS = 12
BUDGET_S = 600.0

#: `ollama.py` 의 손상된 tool_call 센티널 이름과 같은 값이다. 그 모듈이
#: private 상수(`_MALFORMED_TOOL_NAME`)로 두고 있어 여기서는 리터럴로 맞춘다.
_MALFORMED_TOOL_NAME = "__malformed_tool_call__"

#: `OllamaUnavailable` 재시도 백오프(초). 총 4번의 대기로 약 30초를 채운다 —
#: 브리핑에 나온 "모델 로딩은 대개 30초 걸린다"를 커버하면서, 영구적으로
#: 죽어 있는 경우에는 600초 예산의 5% 안쪽에서 포기하도록 짧게 잡았다.
_OLLAMA_RETRY_BACKOFFS_S: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)


class LoopFailed(RuntimeError):
    """작업을 완수하지 못했다. 오케스트레이터의 재시도·환류 상한이 이어받는다."""


@dataclass
class LoopResult:
    payload: dict
    turns: int


def _budget_exceeded_message(budget_s: float) -> str:
    return f"작업 예산 {budget_s}초를 넘겼다"


async def _chat_with_retry(
    llm,
    messages: list[dict],
    schemas: list[dict],
    *,
    now: Callable[[], float],
    started: float,
    budget_s: float,
    sleep: Callable[[float], Awaitable[None]],
):
    """`llm.chat` 을 부르되, 두 가지를 이 루프가 직접 집행한다.

    1. 호출 자체를 남은 예산으로 감싼다 — 느린(또는 멈춘) 호출 하나가 전체
       시간 예산을 크게 넘기지 못하게 한다.
    2. `OllamaUnavailable` 은 턴을 소모하지 않고 백오프로 재시도한다. 재시도가
       바닥나거나 그 사이 예산이 다 떨어지면 `LoopFailed` 로 끝낸다.
    """
    attempts = len(_OLLAMA_RETRY_BACKOFFS_S) + 1
    last_exc: OllamaUnavailable | None = None

    for attempt in range(1, attempts + 1):
        remaining = budget_s - (now() - started)
        if remaining <= 0:
            raise LoopFailed(_budget_exceeded_message(budget_s))

        try:
            return await asyncio.wait_for(llm.chat(messages, schemas), timeout=remaining)
        except TimeoutError:
            raise LoopFailed(_budget_exceeded_message(budget_s)) from None
        except OllamaUnavailable as exc:
            last_exc = exc
            if attempt == attempts:
                break
            remaining = budget_s - (now() - started)
            if remaining <= 0:
                raise LoopFailed(_budget_exceeded_message(budget_s)) from None
            await sleep(min(_OLLAMA_RETRY_BACKOFFS_S[attempt - 1], remaining))

    raise LoopFailed(f"Ollama 를 {attempts}번 시도했지만 계속 쓸 수 없었다: {last_exc}")


def _tool_error_result(name: str, arguments: dict, exc: ToolNotAllowed) -> dict:
    """`ToolNotAllowed` 를 도구 *실행* 실패와 같은 모양의 결과로 바꾼다.

    `arguments` 는 신뢰하지 않는다 — 정상적으로 파싱된 손상 센티널이면 항상
    dict 지만, 모델이 그 이름을 직접 지어 임의의(비-dict 일 수도 있는) 값을
    넣었을 수도 있다.
    """
    if name == _MALFORMED_TOOL_NAME:
        reason = arguments.get("error") if isinstance(arguments, dict) else None
        detail = f"모델이 만든 도구 호출을 해석할 수 없었다: {reason or '원인 불명'}"
    else:
        detail = str(exc)
    return {"ok": False, "detail": detail}


async def run_loop(
    *,
    role: Role,
    llm,
    bridge,
    schemas: list[dict],
    title: str,
    revision: int,
    feedback: list[dict],
    on_tool: Callable[[str, dict, dict], None] | None = None,
    now: Callable[[], float] = time.monotonic,
    max_turns: int = MAX_TURNS,
    budget_s: float = BUDGET_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> LoopResult:
    started = now()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(role)},
        {"role": "user", "content": task_prompt(title, revision, feedback)},
    ]
    verdict_exit_code: int | None = None
    verdict_detail = ""
    turns = 0

    while True:
        if turns >= max_turns:
            raise LoopFailed(f"턴 상한 {max_turns} 을 넘겼다")
        if now() - started > budget_s:
            raise LoopFailed(_budget_exceeded_message(budget_s))

        reply = await _chat_with_retry(
            llm, messages, schemas, now=now, started=started, budget_s=budget_s, sleep=sleep,
        )
        turns += 1

        if not reply.tool_calls:
            break

        messages.append(
            {"role": "assistant", "content": reply.content, "tool_calls": [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls
            ]}
        )
        for call in reply.tool_calls:
            try:
                result = await bridge.call(call.name, call.arguments)
            except ToolNotAllowed as exc:
                result = _tool_error_result(call.name, call.arguments, exc)
            if on_tool is not None:
                on_tool(call.name, call.arguments, result)
            if role.is_verifier and call.name == role.verdict_tool:
                verdict_exit_code = result.get("exit_code")
                verdict_detail = result.get("detail", "")
            messages.append(
                {"role": "tool", "name": call.name,
                 "content": json.dumps(result, ensure_ascii=False)}
            )

    if role.is_verifier and verdict_exit_code is None:
        # 근거 없는 통과를 만드느니 실패시킨다 (스펙 §5.3).
        raise LoopFailed(f"{role.name} 이 판정 도구({role.verdict_tool})를 호출하지 않았다")

    payload: dict = {"kind": role.artifact_kind, "agent": role.name}
    if role.is_verifier:
        payload["exit_code"] = verdict_exit_code
        payload["verdict"] = "PASS" if verdict_exit_code == 0 else "FAIL"
        payload["summary"] = reply.content or verdict_detail
    else:
        payload["exit_code"] = 0
        payload["summary"] = reply.content
    return LoopResult(payload=payload, turns=turns)
