from __future__ import annotations
from dataclasses import dataclass, field
import yaml


class DuplicateCallError(RuntimeError):
    """같은 멱등성 키로 두 번 호출됐다. 오케스트레이터의 멱등성 위반."""


@dataclass
class AgentScenario:
    agent: str
    verdicts: list[str] = field(default_factory=list)
    failures: dict[int, str] = field(default_factory=dict)
    latency_ms: int = 0
    _cursor: int = 0
    _seen_keys: dict[str, int] = field(default_factory=dict)
    _invocation: int = 0

    @classmethod
    def from_yaml(cls, path: str, agent: str) -> "AgentScenario":
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        spec = doc.get("agents", {}).get(agent, {}) or {}
        failures = {
            int(k.removeprefix("attempt_")): v
            for k, v in spec.items() if k.startswith("attempt_")
        }
        return cls(agent=agent, verdicts=list(spec.get("verdicts", [])),
                   failures=failures, latency_ms=int(spec.get("latency_ms", 0)))

    def next_verdict(self) -> str | None:
        if not self.verdicts:
            return None
        idx = min(self._cursor, len(self.verdicts) - 1)
        self._cursor += 1
        return self.verdicts[idx]

    def failure_for_attempt(self, n: int) -> str | None:
        return self.failures.get(n)

    def next_attempt(self) -> int:
        """이 시나리오(에이전트)에 대한 스텁 자신의 호출 순번(1부터 증가).

        오케스트레이터의 재시도 횟수(`workflow_tasks.attempt`)와는 다른 카운터다 —
        `attempt_N` 시나리오 키는 이 순번을 가리킨다: 스텁이 N번째로 실행됐을 때
        무엇을 하는지를 뜻한다.
        """
        self._invocation += 1
        return self._invocation

    def record_call(self, idempotency_key: str) -> int:
        if idempotency_key in self._seen_keys:
            raise DuplicateCallError(f"중복 호출: {idempotency_key}")
        self._seen_keys[idempotency_key] = 1
        return 1
