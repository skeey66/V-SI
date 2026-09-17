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


class ScenarioError(ValueError):
    """시나리오 파일이 이 스텁이 모르는 것을 지시했다.

    **조용히 무시하지 않는 것이 요점이다.** 시나리오는 실패 주입 테스트의
    명세다 — `attempt_2: hang`처럼 오타 난 지시를 무시하면 그 테스트는
    **실패를 주입하지 않은 채** 초록이 된다. 아무것도 시험하지 않으면서
    통과하는 테스트가 깨진 테스트보다 나쁘다.
    """


#: `executor.build_payload`가 실제로 해석하는 주입 모드. 여기 없는 값은
#: executor의 `== "crash"` 비교에 걸리지 않아 아무 일도 일으키지 못한다.
KNOWN_FAILURE_MODES = frozenset({"crash"})

#: `attempt_N` 외에 에이전트 스펙이 가질 수 있는 키.
KNOWN_SPEC_KEYS = frozenset({"verdicts", "latency_ms"})

_ATTEMPT_PREFIX = "attempt_"


def _parse_failures(spec: dict, agent: str, path: str) -> dict[int, str]:
    """`attempt_N` 키를 걷어 내고, 나머지가 전부 아는 키인지 확인한다."""
    failures: dict[int, str] = {}
    for key, value in spec.items():
        if not key.startswith(_ATTEMPT_PREFIX):
            if key not in KNOWN_SPEC_KEYS:
                raise ScenarioError(
                    f"{path}의 {agent}: 알 수 없는 키 {key!r} "
                    f"(아는 키: {sorted(KNOWN_SPEC_KEYS)} 또는 "
                    f"{_ATTEMPT_PREFIX}N). 'attempt2'처럼 밑줄을 빠뜨리지 "
                    f"않았는지 확인할 것 — 그 오타는 주입을 통째로 삼킨다"
                )
            continue
        suffix = key.removeprefix(_ATTEMPT_PREFIX)
        if not suffix.isdigit():
            raise ScenarioError(
                f"{path}의 {agent}: {key!r}의 N이 숫자가 아니다"
            )
        if value not in KNOWN_FAILURE_MODES:
            raise ScenarioError(
                f"{path}의 {agent}: {key}에 알 수 없는 주입 모드 {value!r} "
                f"(아는 모드: {sorted(KNOWN_FAILURE_MODES)}). 이 값은 스텁이 "
                f"해석하지 못해 아무 실패도 주입되지 않는다"
            )
        failures[int(suffix)] = value
    return failures


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
        failures = _parse_failures(spec, agent, path)
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
