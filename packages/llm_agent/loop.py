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
되돌린다. **`bridge.call` 이 던질 수 있는 다른 모든 예외도 마찬가지다** —
끊긴 MCP 세션, 시간 초과, 네트워크 오류 같은 것이 허용 목록 위반보다 실제로
훨씬 흔하다. 이 파일에는 `bridge.call` 의 어떤 예외도 대화 루프를 벗어나
죽이는 경로가 없다. 도구 결과가 (버그로) dict 가 아니거나 `json` 이 그대로
못 삼키는 값을 담고 있어도 마찬가지로 죽지 않는다.

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

**같은 패턴을 도구 호출에도 대칭으로 적용한다.** 응답 하나에 도구 호출이
여러 개 들어올 수 있고(모델이 몇 개를 담을지는 이 루프가 정하지 않는다),
`while` 의 다음 반복까지는 그 도구 호출들을 하나도 거르지 않고 다 돈다 —
그 반복 시작 시점의 예산 검사만 믿으면 응답 하나가 예산을 몇 배로 넘길 수
있다. 그래서 `bridge.call` 하나하나 앞에서 남은 예산을 다시 재고, 이미
바닥났으면 그 자리에서 `LoopFailed` 로 끝낸다(같은 응답의 나머지 호출은
아예 시도하지 않는다). 남은 예산이 있으면 호출 자체도 그 예산으로 감싼다 —
멈춘 도구 호출 하나가 그 예산을 다 태우면 `chat()` 과 마찬가지로 잘린다.
다만 그 잘림은 예외가 아니라 도구 메시지로 대화에 들어간다 — 이미 모델의
`tool_calls` 턴이 시작된 뒤라 되돌릴 대화가 있고, 모델이 그걸 보고 반응할
기회를 얻는다(바로 다음 도구 호출이나 다음 `while` 반복에서 예산이 이미
바닥났다는 걸 어차피 잡아낸다).

검증자의 `verdict` 는 도구 종료코드에서만 온다 (스펙 §5.3). 모델 응답 텍스트는
`summary` 로만 들어가고 판정에 영향을 주지 않는다 — 이 파일에 그 경로가 없다.

검증자가 판정 도구를 한 번도 안 부르고 턴을 끝내는 것도(예: qwen3:8b 가 도구
호출 없이 텍스트만으로 답하고 끝낸 실제 관측 사례) 위와 같은 모양의 교정
가능한 실수로 다룬다: 예외로 바로 죽이지 않고 "판정은 도구에서만 나온다"는
계약을 메시지로 돌려주고 `MAX_VERDICT_NUDGES` 번까지 다시 턴을 준다. 그
상한을 다 쓰고도 도구를 안 부르면 그제서야 `LoopFailed` 다 — 근거 없는
판정을 만들지 않는다는 원칙은 그대로 지킨다.
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

#: 도구 호출 하나의 상한(초). 남은 예산(수백 초일 수 있다)을 그대로 타임아웃
#: 으로 쓰면, 멈춘 도구 호출 하나가 사실상 예산 전체를 태우고서야(그것도
#: 예외가 아니라 메시지로) 끝난다 — 다음 `while` 반복의 예산 검사가 항상
#: 먼저 걸려 그 메시지가 모델에게 갈 기회조차 없다. 그래서 `min(remaining,
#: TOOL_CALL_CAP_S)` 로 짧게 자른다. 도구 서버의 `SUBPROCESS_TIMEOUT_S`(60초,
#: `services/tool_server/tools.py`)가 정상적인 `run_tests`/`run_security_scan`
#: 실행의 실질 상한이므로, MCP 왕복·직렬화 오버헤드를 감안한 여유를 더해
#: 90초로 잡는다 — 정상적인 느린 호출은 여유 있게 끝나고, 진짜로 멈춘
#: 호출은 600초 예산의 15% 안쪽에서 잘려 모델이 다음 턴을 받는다.
TOOL_CALL_CAP_S = 90.0

#: `ollama.py` 의 손상된 tool_call 센티널 이름과 같은 값이다. 그 모듈이
#: private 상수(`_MALFORMED_TOOL_NAME`)로 두고 있어 여기서는 리터럴로 맞춘다.
_MALFORMED_TOOL_NAME = "__malformed_tool_call__"

#: 검증자가 판정 도구를 한 번도 안 부르고 턴을 끝냈을 때, 다시 기회를 주는
#: 횟수의 상한. 이 값은 `max_turns` 와 별개로 작다 — `max_turns` 는 정상적인
#: 도구 호출 반복(list_files → read_file → run_tests 같은) 을 감안해 넉넉히
#: 잡혀 있는데, 그 예산을 "판정 도구를 아예 안 부른다"는 한 가지 실수를
#: 봐주는 데 전부 쓰게 두면 안 된다. 매 nudge 는 `chat()` 을 한 번 더
#: 부르므로(워밍업 상태에서도 12~15초) 시간 예산도 갉아먹는다. 2번이면
#: "한 번은 안내문처럼 답하고 끝냈을 수도 있다"는 흔한 실수는 봐주면서도,
#: 스스로 회복 못 하는 모델을 세 번째 시도에서 확정 실패시켜 나머지
#: 에이전트(dev, 그리고 오케스트레이터의 재시도)에게 예산을 돌려준다.
MAX_VERDICT_NUDGES = 2

#: `OllamaUnavailable` 재시도 백오프(초). 총 4번의 대기로 약 30초를 채운다 —
#: 브리핑에 나온 "모델 로딩은 대개 30초 걸린다"를 커버하면서, 영구적으로
#: 죽어 있는 경우에는 600초 예산의 5% 안쪽에서 포기하도록 짧게 잡았다.
_OLLAMA_RETRY_BACKOFFS_S: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)

#: 산출물 스냅샷(`payload["files"]`)에 담는 파일 하나의 최대 크기(바이트).
#: `read_file`(`services/tool_server/tools.py`, `READ_LIMIT_BYTES`)과 똑같은
#: 32KB를 쓴다 — 그 값은 이미 "8b 모델의 컨텍스트에 넣기에/DB 컬럼 하나에
#: 넣기에 적당한 파일 하나 크기"로 검증된 상수다. 새 숫자를 따로 만들면
#: "이 파일은 왜 32KB가 아니라 다른 값에서 잘리는가"라는 새 질문만 생긴다.
FILE_SNAPSHOT_PER_FILE_CAP_BYTES = 32_768

#: 산출물 스냅샷 전체(한 리비전이 쓴 모든 파일의 합)의 최대 크기(바이트).
#: 파일당 상한의 약 6배로 잡는다 — 실제 리비전이 만드는 파일 수(`spec.md` +
#: `test_*.py` 1~2개 + 구현 파일 1~2개, 실측 4~6개 안팎)를 손실 없이 담고도
#: 여유가 남으면서, 모델이 폭주해 파일을 수십 개 쓰거나 파일 하나를 반복
#: 텍스트로 채우는 사고가 `Artifact.content`(JSONB 컬럼)를 무한정 불리지
#: 못하게 막는다.
FILE_SNAPSHOT_TOTAL_CAP_BYTES = 200_000


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
        except Exception as exc:
            # `OllamaUnavailable` 이 아닌 다른 chat() 실패(404 같은 설정 실수,
            # 응답을 JSON 으로 못 읽는 경우 등)는 로컬 재시도로 풀리지 않는다.
            # 여기서 한 번에 `LoopFailed` 로 바꿔서, 이 루프를 나가는 실패
            # 타입을 하나로 유지한다.
            raise LoopFailed(f"Ollama 호출이 실패했다: {exc}") from exc

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


def _bridge_exception_result(name: str, exc: Exception) -> dict:
    """`ToolNotAllowed` 이외의 `bridge.call` 예외(끊긴 세션, 네트워크 오류
    등)도 도구 실행 실패와 같은 모양으로 바꾼다."""
    return {"ok": False, "detail": f"'{name}' 호출이 예외로 끝났다: {exc}"}


def _verdict_nudge_message(role: Role) -> str:
    """검증자가 판정 도구 없이 턴을 끝냈을 때 되돌려줄 계약 진술.

    애원("제발 도구를 불러줘")이 아니라 계약을 적는다: 판정이 어디서
    나오는지, 이번 턴에 무엇이 빠졌는지, 무엇을 해야 하는지. 모델이 읽고
    고칠 수 있는 도구 오류 메시지와 같은 성격이다 (스펙 §6.1) — 다만 도구
    실행이 실패한 게 아니라 도구를 아예 안 부른 것이므로 `role="tool"` 이
    아니라 `role="user"` 로 넣는다.
    """
    return (
        f"{role.name} 역할의 판정(PASS/FAIL)은 `{role.verdict_tool}` 도구의 "
        f"종료코드에서만 나온다 — 텍스트로 적은 결론은 판정으로 치지 않는다. "
        f"이번 턴에서 `{role.verdict_tool}` 을 호출하지 않았다. "
        f"`{role.verdict_tool}` 을 호출해서 판정 근거를 만들어라."
    )


async def _call_tool(
    bridge,
    name: str,
    arguments: dict,
    *,
    now: Callable[[], float],
    started: float,
    budget_s: float,
    tool_call_cap_s: float,
) -> dict:
    """`bridge.call` 을 호출하되, `chat()` 과 대칭인 예산 집행을 적용한다.

    예산이 이미 바닥났으면 즉시 `LoopFailed` 다 — 다음 `while` 반복까지
    기다리면 같은 응답에 남은 호출들이 예산을 몇 배로 넘길 수 있다. 예산이
    남아 있으면 호출 자체는 `min(남은 예산, tool_call_cap_s)` 로 감싼다 —
    남은 예산을 그대로 쓰면(수백 초일 수 있다) 멈춘 호출이 사실상 예산
    전체를 태우고, 그 시점엔 이미 다음 반복의 예산 검사가 먼저 걸려 아래
    타임아웃 메시지가 모델에게 갈 기회조차 없다. 짧은 상한으로 잘라야 그
    메시지가 실제로 전달되고 모델이 다음 턴에 반응할 수 있다. 이 호출 안에서
    나는 다른 모든 실패(허용되지 않은 도구, 시간 초과, 세션 예외, 네트워크
    오류 등)는 예외가 아니라 도구 메시지로 바뀐다 (스펙 §6.1).
    """
    remaining = budget_s - (now() - started)
    if remaining <= 0:
        raise LoopFailed(_budget_exceeded_message(budget_s))
    try:
        return await asyncio.wait_for(
            bridge.call(name, arguments), timeout=min(remaining, tool_call_cap_s)
        )
    except TimeoutError:
        return {"ok": False, "detail": f"'{name}' 호출이 {tool_call_cap_s}초 안에 끝나지 않았다"}
    except ToolNotAllowed as exc:
        return _tool_error_result(name, arguments, exc)
    except Exception as exc:
        return _bridge_exception_result(name, exc)


def _capture_write(files: dict[str, str], arguments: dict) -> bool:
    """성공한 `write_file` 호출의 인자에서 `path -> content` 를 뽑아 누적한다.

    워크스페이스는 볼륨이 없어 `docker compose down` 과 함께 사라진다
    (`docker-compose.yml`) — DB 의 `Artifact.content` 만이 "리비전이 무엇을
    냈는가"에 답할 수 있는 유일한 기록이다 (스펙 §6.3/§5.2). 워크스페이스를
    되읽지 않고 **호출 인자**를 그대로 쓰는 이유: planner 의 도구 집합에는
    `read_file`/`list_files` 가 없다(역할별 권한 분리, 스펙 §5.1 — 이 리뷰의
    지시로 넓히지 않는다). 되읽기가 아니라 "에이전트가 실제로 무엇을 썼다고
    선언했는가"를 기록하는 편이 "3회차가 무엇을 냈는가"라는 질문에도 더
    정직한 답이다.

    같은 경로에 대한 나중 쓰기가 이전 것을 대체한다(히스토리가 아니라 이
    리비전의 최종 내용을 남긴다). 개별 파일 상한과 스냅샷 전체 상한을 모두
    적용하고, 둘 중 하나라도 잘랐으면 `True` 를 돌려준다 — 호출자가 이 값을
    모아 payload 에 "잘렸다"고 정직하게 적는다(조용히 완전한 척하지 않는다).
    """
    path = arguments.get("path") if isinstance(arguments, dict) else None
    content = arguments.get("content") if isinstance(arguments, dict) else None
    if not isinstance(path, str) or not isinstance(content, str):
        # 모델이 지어낸 인자거나 파싱이 이상한 경우다. `write_file` 자체는
        # 이미 성공했으므로(이 함수는 result["ok"] 가 True 일 때만 불린다)
        # 실행에는 영향이 없다 — 신뢰할 (path, content) 쌍이 없어 스냅샷에서만
        # 조용히 건너뛴다.
        return False

    truncated = False
    raw = content.encode("utf-8", errors="replace")
    if len(raw) > FILE_SNAPSHOT_PER_FILE_CAP_BYTES:
        content = (
            raw[:FILE_SNAPSHOT_PER_FILE_CAP_BYTES].decode("utf-8", errors="replace")
            + "\n...(파일당 상한 초과로 뒷부분 절단)...\n"
        )
        truncated = True

    files[path] = content
    total = sum(len(v.encode("utf-8", errors="replace")) for v in files.values())
    if total > FILE_SNAPSHOT_TOTAL_CAP_BYTES:
        # 여기서는 안내 문구를 덧붙이지 않는다 — 마커 자체도 바이트를 차지
        # 하므로, 상한을 넘길 때마다 매번 붙이면(파일이 아주 많을 때 특히)
        # 잘라도 잘라도 그 마커 바이트만큼씩 다시 상한을 넘기는 무한 초과가
        # 난다. "잘렸다"는 사실은 payload 수준의 `files_truncated` 플래그가
        # 이미 정직하게 알리므로, 여기서는 총합 상한을 정확히 지키는 쪽을
        # 택한다(파일당 상한과 달리 마커를 넣지 않는다).
        overflow = total - FILE_SNAPSHOT_TOTAL_CAP_BYTES
        current_raw = files[path].encode("utf-8", errors="replace")
        keep = max(0, len(current_raw) - overflow)
        if keep == 0:
            # 스냅샷이 이미 포화됐다. 여기서 빈 문자열을 넣으면 payload 가
            # "이 파일을 빈 내용으로 썼다"고 **적극적으로 거짓말**하게 된다 —
            # 에이전트가 실제로 빈 파일을 쓴 경우와 구별할 수 없다. 기록을
            # 아예 빼는 쪽이 정직하다. 잘렸다는 사실은 `files_truncated` 가 알린다.
            files.pop(path, None)
        else:
            files[path] = current_raw[:keep].decode("utf-8", errors="replace")
        truncated = True
    return truncated


async def run_loop(
    *,
    role: Role,
    llm,
    bridge,
    schemas: list[dict],
    title: str,
    revision: int,
    feedback: list[dict],
    on_tool: Callable[[str, dict, dict], Awaitable[None]] | None = None,
    now: Callable[[], float] = time.monotonic,
    max_turns: int = MAX_TURNS,
    budget_s: float = BUDGET_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tool_call_cap_s: float = TOOL_CALL_CAP_S,
) -> LoopResult:
    started = now()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(role)},
        {"role": "user", "content": task_prompt(title, revision, feedback)},
    ]
    verdict_exit_code: int | None = None
    verdict_detail = ""
    turns = 0
    verdict_nudges_used = 0
    #: 이번 태스크 전체에서 실제로 시도된 도구 호출 개수(성공/실패 무관). 0
    #: 이면 모델이 프리텍스트만 남기고 아무것도 안 한 것 — 스펙 §5.3/§10
    #: 실패표가 무조건 EXECUTION 실패로 두는 경우다.
    tool_calls_made = 0
    #: dev/planner(비검증자)의 `write_file` 호출 인자에서 뽑은 `path ->
    #: content` 최종 스냅샷. 검증자는 애초에 `write_file` 권한이 없어 항상
    #: 비어 있는다 — payload 조립 시점에 역할별로 포함 여부를 가른다.
    files: dict[str, str] = {}
    files_truncated = False

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
            # 검증자가 판정 도구를 한 번도 안 부르고 턴을 끝냈다 — 근거 없는
            # 판정을 만들 수는 없지만(스펙 §5.3), 이는 다른 모든 교정 가능한
            # 실수와 같은 모양이다: 도구 오류가 예외 대신 메시지가 되어
            # 모델이 보고 고칠 기회를 얻는 것처럼(스펙 §6.1), "너는 검증자인데
            # 판정 도구 없이 턴을 끝냈다"도 메시지로 돌려주고 다시 시도하게
            # 한다. `max_turns`/`budget_s` 와 별개로 `MAX_VERDICT_NUDGES` 로
            # 추가로 상한을 둔다 — 그러지 않으면 협조하지 않는 검증자 하나가
            # 정상적인 도구 호출 반복을 감안해 넉넉히 잡힌 턴·시간 예산 전체를
            # "판정 도구를 안 부른다"는 같은 실수를 반복하는 데 다 쓸 수 있다.
            if (
                role.verdict_tool is not None
                and verdict_exit_code is None
                and verdict_nudges_used < MAX_VERDICT_NUDGES
            ):
                verdict_nudges_used += 1
                messages.append({"role": "assistant", "content": reply.content})
                messages.append({"role": "user", "content": _verdict_nudge_message(role)})
                continue
            break

        messages.append(
            {"role": "assistant", "content": reply.content, "tool_calls": [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls
            ]}
        )
        for call in reply.tool_calls:
            tool_calls_made += 1
            result = await _call_tool(
                bridge, call.name, call.arguments,
                now=now, started=started, budget_s=budget_s, tool_call_cap_s=tool_call_cap_s,
            )
            if not isinstance(result, dict):
                # 브리지가 계약(-> dict)을 어겨도 여기서 죽지 않는다 — 근거
                # 없는 통과를 만들 수는 없으니 실패로 취급한다.
                result = {"ok": False, "detail": f"도구가 dict 가 아닌 값을 돌려줬다: {result!r}"}
            if on_tool is not None:
                # `on_tool` 은 awaitable 계약이다(코루틴 함수만 받는다) — sync
                # 콜백을 관대하게 받아주면, 실수로 넘긴 코루틴 함수를 그냥
                # `await` 없이 부르는 사고를 그대로 재현하게 된다: 코루틴은
                # 게을러서 본문이 한 줄도 안 돌고, 예외도 없이 조용히
                # 사라진다(가비지 컬렉션 시점에만 비결정적으로
                # `RuntimeWarning` 이 뜬다). 여기서 무조건 `await` 해서 그
                # 실패 모드를 구조적으로 없앤다 — 대신 sync 콜백을 넘기면
                # `await None` 이 즉시 `TypeError` 로 죽어, 계약 위반이 첫
                # 호출에서 바로 드러난다.
                await on_tool(call.name, call.arguments, result)
            if role.verdict_tool is not None and call.name == role.verdict_tool:
                verdict_exit_code = result.get("exit_code")
                verdict_detail = result.get("detail", "")
            # 파일 스냅샷은 **`write_file` 권한이 있는 역할**이면 남긴다.
            # 예전에는 `not role.is_verifier` 로 갈랐는데, 기획이 판정 도구를
            # 갖게 되면서 그 조건이 두 가지 서로 다른 질문("판정을 내는가"와
            # "파일을 쓰는가")을 하나로 뭉뚱그리게 됐다. 기획은 판정 도구를
            # 가지면서 동시에 파일을 쓰는 유일한 역할이다.
            if "write_file" in role.tools and call.name == "write_file" and result.get("ok"):
                # 성공한 쓰기만 산출물이다 — 경로 탈출 등으로 거부된
                # 쓰기(`ok: False`)는 실행이 실패한 것이므로 스냅샷에
                # 들어가면 안 된다.
                if _capture_write(files, call.arguments):
                    files_truncated = True
            messages.append(
                {"role": "tool", "name": call.name,
                 "content": json.dumps(result, ensure_ascii=False, default=str)}
            )

    if tool_calls_made == 0:
        # 도구 호출 0회는 역할과 무관하게 EXECUTION 실패다 (스펙 §5.3, §10
        # 실패표). 예전에는 검증자만 이 검사를 받았다 — 비검증자(planner/dev)
        # 는 `while` 루프가 그냥 `break` 하고 호출부(`run_loop` 뒤)가
        # `exit_code = 0`을 찍어, 모델이 프리텍스트만 남기고 아무 파일도 안
        # 써도 "성공"으로 기록됐다(`executor.py` 의 `McpRoleMismatch` 독스트링이
        # 짚은 바로 그 "성공으로 위장한 무동작"과 같은 모양이다). 검증자가
        # nudge 를 다 쓰고도 도구를 하나도 안 부른 경우도 이 조건을 만족해
        # 여기서 먼저 걸린다 — 아래의 "판정 도구를 안 불렀다"는 더 구체적인
        # 메시지는 "도구는 불렀지만 판정 도구는 아니었다"는 경우에만 나온다.
        raise LoopFailed(f"{role.name} 이 도구를 한 번도 호출하지 않고 끝났다")

    if role.verdict_tool is not None and verdict_exit_code is None:
        # 근거 없는 통과를 만드느니 실패시킨다 (스펙 §5.3).
        raise LoopFailed(f"{role.name} 이 판정 도구({role.verdict_tool})를 호출하지 않았다")

    payload: dict = {"kind": role.artifact_kind, "agent": role.name}
    if role.verdict_tool is not None:
        payload["exit_code"] = verdict_exit_code
        payload["verdict"] = "PASS" if verdict_exit_code == 0 else "FAIL"
        payload["summary"] = reply.content or verdict_detail
    else:
        payload["exit_code"] = 0
        payload["summary"] = reply.content

    # 검증자(qa/security)는 `write_file` 권한 자체가 없다(스펙 §5.1) — 그런
    # 역할의 payload에 항상 빈 `files: {}`를 넣느니, 이 산출물 종류엔 "쓴 파일"
    # 이라는 개념이 아예 없다는 뜻으로 키를 통째로 뺀다(present-and-empty 가
    # 아니라 absent를 선택했다).
    if "write_file" in role.tools:
        payload["files"] = files
        if files_truncated:
            payload["files_truncated"] = True
    return LoopResult(payload=payload, turns=turns)
