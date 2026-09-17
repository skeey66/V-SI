"""시나리오 파일 — 스텁 에이전트의 결정적 대본.

`verdicts`(회차별 판정 순서), `latency_ms`(지연), `attempt_N`(N번째 호출에
주입할 실패)만 있다. LLM이 없으므로 이 파일이 에이전트 행동의 전부다.

**멱등성 키를 여기서 검사하지 않는다.** 예전에는 같은 키로 두 번 불리면
터지는 `record_call`이 있었지만, 그 검출기는 아무도 부르지 않는 죽은 코드였다
(스펙 §8이 그때까지 "에이전트가 키로 중복을 제거한다"고 적고 있었을 뿐이다).
그 자리 재시도가 **증명된 미전달**로 좁혀진 뒤로는 같은 키가 에이전트에 두 번
도달하는 경로 자체가 없어, 검출기가 불필요해졌다 — 미구현이 아니라 무의미해진
것이다. 그 전제가 깨지는 조건은 스펙 §8과 §12.1에 경고로 남아 있다.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import yaml


@dataclass
class AgentScenario:
    agent: str
    verdicts: list[str] = field(default_factory=list)
    failures: dict[int, str] = field(default_factory=dict)
    latency_ms: int = 0
    _cursor: int = 0
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
