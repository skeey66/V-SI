"""실패 5분류와 재시도 정책.

층이 다른 두 실패군을 구분한다:

- **전송(TRANSPORT)·실행(EXECUTION)·독성입력(POISON)** — 디스패치 층. "같은 일을
  다시 시도"할지 말지를 결정한다. 이 세 가지가 `FailureClass`다.
- **품질 반려(QA FAIL)·정책 차단(정책 게이트)** — 워크플로 층. "다른 일을
  시작"한다(환류/에스컬레이션). 이미 `workflow.py`의 신호(`VERDICTS_FAIL`,
  `APPROVAL_REQUIRED`)로 표현되어 있고, 이 모듈이 다루는 대상이 아니다.

**독성 입력은 재시도하지 않는다**(`max_attempts(POISON) == 1`). 입력 자체가
잘못됐으면 같은 요청을 다시 보내도 결정적으로 같은 결과가 나온다 — 재시도는
그저 느린 실패를 만들 뿐이다.
"""

from __future__ import annotations

from enum import StrEnum

import httpx


class FailureClass(StrEnum):
    TRANSPORT = "transport"
    EXECUTION = "execution"
    POISON = "poison"


#: 클래스별 최대 시도 횟수(최초 시도 포함).
_MAX_ATTEMPTS: dict[FailureClass, int] = {
    FailureClass.TRANSPORT: 3,
    FailureClass.EXECUTION: 2,
    FailureClass.POISON: 1,  # 재시도해도 같은 실패가 확정적이다.
}

#: 지수 백오프의 상한(초). 느린 실패가 회로를 영원히 점유하지 않게 한다.
_BACKOFF_CAP_S = 8.0
_BACKOFF_BASE_S = 0.5


def classify(exc: Exception) -> FailureClass:
    """예외 하나를 다섯 분류 중 디스패치 층의 셋으로 분류한다.

    - `httpx.HTTPStatusError`: 4xx는 클라이언트가 보낸 요청 자체가 문제(POISON),
      5xx는 상대가 일시적으로 응답하지 못한 것(TRANSPORT)으로 본다.
    - 연결·타임아웃·네트워크 오류는 TRANSPORT. 엔진이 개별 submit 시도를
      `TimeoutConfig.tool_s`로 감싸므로(Task 12), `asyncio.wait_for`가 던지는
      내장 `TimeoutError`도 같은 층으로 취급한다.
    - 스키마·타입 문제(`ValueError`/`TypeError`/`KeyError`)는 입력 자체가
      잘못됐다는 신호라 POISON.
    - 그 외는 실행 중 원인 불명 오류로 EXECUTION(예: executor 크래시).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            FailureClass.POISON
            if 400 <= exc.response.status_code < 500
            else FailureClass.TRANSPORT
        )
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, TimeoutError)):
        return FailureClass.TRANSPORT
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return FailureClass.POISON
    return FailureClass.EXECUTION


def is_undelivered(exc: Exception) -> bool:
    """이 예외가 **요청이 상대에게 닿지 않았다는 것을 증명하는가**.

    Task 12 수정: `dispatch_agent`의 제자리 재시도(같은 행, 같은 멱등성 키)는
    상대가 이전 요청을 이미 받았을 가능성이 0일 때만 안전하다. 연결 자체가
    거부됐거나(포트가 안 열려 있다) TCP 연결 수립 자체가 타임아웃났다면
    바이트 하나도 나가지 않았다는 뜻이라 그 자리에서 다시 보내도 에이전트
    쪽에 중복 Task가 생기지 않는다.

    그 외는 전부 모호하다고 본다 — 읽기 타임아웃(요청은 갔고 응답만 못 받음),
    5xx(요청을 받고 나서 실패했을 수 있음), `asyncio.wait_for`의 내장
    `TimeoutError`(제출 왕복 전체를 감싸므로 에이전트가 이미 작업을 받아 처리
    중이었을 가능성을 배제 못함) 모두 여기 해당한다. 모호한 실패는 제자리
    재시도 대상이 아니다 — 이 행을 실패로 확정하고, 리컨실러가 **새 행·새
    멱등성 키**로 다시 보내게 한다(`reconciler.next_action`의 DISPATCH).
    """
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def max_attempts(fc: FailureClass) -> int:
    """이 실패 분류에서 허용하는 최대 시도 횟수(최초 시도 포함)."""
    return _MAX_ATTEMPTS[fc]


def backoff_seconds(attempt: int) -> float:
    """`attempt`(1부터)번째 실패 뒤 다음 시도까지 기다릴 시간. 지수 백오프."""
    return min(_BACKOFF_BASE_S * (2 ** (attempt - 1)), _BACKOFF_CAP_S)
