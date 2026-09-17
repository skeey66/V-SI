from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping


class TimeoutConfigError(ValueError):
    """타임아웃 계층이 안쪽 < 바깥쪽 부등식을 위반했다."""


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
        cfg = cls(
            tool_s=int(env.get("VSI_TIMEOUT_TOOL_S", 30)),
            executor_s=int(env.get("VSI_TIMEOUT_EXECUTOR_S", 300)),
            step_s=int(env.get("VSI_TIMEOUT_STEP_S", 900)),
            run_s=int(env.get("VSI_TIMEOUT_RUN_S", 3600)),
        )
        cfg.validate()
        return cfg
