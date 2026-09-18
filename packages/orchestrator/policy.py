"""타임아웃 계층.

네 층이 안쪽 < 바깥쪽 부등식을 이루고, 그 부등식을 **설정 로딩 시점에** 검증해
위반하면 기동을 실패시킨다(스펙 §8). 바깥이 먼저 터지면 안쪽 실패의 원인을 잃고
트레이스에 원인 없는 취소만 남기 때문이다.

**어디까지 실제로 강제되는지**(SP1 기준, 부등식이 "검증된다"와 "집행된다"를
혼동하지 않도록 명시한다):

| 층 | 값 | SP1에서의 배선 |
|---|---|---|
| `tool_s` | 30초 | **집행됨.** 에이전트로 나가는 왕복 하나를 `asyncio.wait_for`로 감싼다 (`engine.dispatch_agent`의 `submit`, `engine.refresh_task`의 `get_task`). |
| `executor_s` | 5분 | **집행됨.** 공유 httpx 클라이언트의 요청당 타임아웃 (`orchestrator/main.py`). `tool_s`가 먼저 끊으므로 실질적으로는 backstop이다. |
| `step_s` | 15분 | 워크플로 단계 예산. SP1에서 한 단계는 스텁이라 초 단위로 끝나고, 멈춘 단계는 리컨실러의 `stuck_after_s` 천장이 끊는다 — 그래서 이 값에 걸릴 일이 없다. |
| `run_s` | 60분 | **SP2 자리표시자.** 지금 이 값을 집행하는 코드는 없다. |

`step_s`·`run_s`를 배선하지 않은 이유는 SP1의 종료 보장이 이미 **다른 축**에
서 있기 때문이다: 열린 행의 나이 천장(`stuck_after_s`)과 실패 행 수의 재시도
캡이 모든 요구사항을 종료 상태로 몰아간다. 시간 예산을 추가로 집행하면 같은
보장을 두 곳에서 서로 다른 기준으로 내리게 되고, 둘이 어긋나는 순간 어느
쪽이 요구사항을 끝냈는지 운영자가 알 수 없다. SP2에서 실제 LLM 지연이 붙어
"느린 것"과 "멈춘 것"의 구분이 나이만으로는 어려워지면 그때 `run_s`를
리컨실러의 요구사항 나이 backstop으로 배선한다.
"""

from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping


class TimeoutConfigError(ValueError):
    """타임아웃 설정이 잘못됐다 — 부등식 위반이거나, 값이 숫자가 아니다."""


@dataclass(frozen=True)
class TimeoutConfig:
    tool_s: int
    executor_s: int
    step_s: int
    run_s: int

    def validate(self) -> None:
        layers = [("tool_s", self.tool_s), ("executor_s", self.executor_s),
                  ("step_s", self.step_s), ("run_s", self.run_s)]
        for (inner_name, inner), (outer_name, outer) in zip(layers, layers[1:]):
            if inner >= outer:
                raise TimeoutConfigError(
                    f"{outer_name}({outer})는 {inner_name}({inner})보다 커야 한다"
                )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "TimeoutConfig":
        """환경변수에서 읽고 부등식까지 검증한다. 설정 오류는 전부 같은 타입이다.

        이 메서드는 `orchestrator/main.py`의 **import 시점**에 돈다 — 오타 난
        값에 맨 `ValueError`가 나가면 컨테이너가 "설정이 틀렸다"가 아니라 정체
        불명의 예외로 죽고, 어느 변수가 문제인지도 메시지에 없다. 잘못된 값과
        잘못된 부등식은 원인이 같으므로(설정 오류) 같은 타입·같은 해상도로
        보고한다.
        """
        def _int(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise TimeoutConfigError(
                    f"{name}는 정수(초)여야 한다: {raw!r}"
                ) from None

        cfg = cls(
            tool_s=_int("VSI_TIMEOUT_TOOL_S", 30),
            executor_s=_int("VSI_TIMEOUT_EXECUTOR_S", 300),
            step_s=_int("VSI_TIMEOUT_STEP_S", 900),
            run_s=_int("VSI_TIMEOUT_RUN_S", 3600),
        )
        cfg.validate()
        return cfg
