from __future__ import annotations
import time

from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part

from stub_agent.scenario import AgentScenario

ARTIFACT_KIND: dict[str, str] = {
    "planner": "requirements",
    "dev": "source_code",
    "qa": "test_report",
    "security": "security_report",
}


class StubCrash(RuntimeError):
    """시나리오가 주입한 크래시."""


def _payload_to_part(payload: dict) -> Part:
    """dict를 A2A `Part`(protobuf `Value`)로 감싼다."""
    value = struct_pb2.Value()
    ParseDict(payload, value)
    return Part(data=value)


class StubExecutor(AgentExecutor):
    """LLM을 호출하지 않는다. 시나리오가 지시한 결과만 만든다.

    구현자 주의(브리프 대비 실제 SDK 1.1.2 시그니처 차이):
    - `AgentExecutor`는 `execute` 외에 `cancel`도 추상 메서드다(브리프 스케치는
      `execute`만 언급했으나, 상속하려면 둘 다 구현해야 한다).
    - `EventQueue.enqueue_event(event)`는 브리프 스케치와 동일하지만, 실제로는
      `Message`/`Task`/`TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` 중
      하나의 이벤트 객체를 기대한다. 원시 dict를 그대로 enqueue할 수 없으므로
      `TaskUpdater` 헬퍼로 표준 이벤트를 만든다.
    - Payload(dict)를 아티팩트에 담으려면 `a2a.types.Part(data=...)`가 필요하고,
      `data` 필드는 protobuf `google.protobuf.Value`이므로
      `google.protobuf.json_format.ParseDict`로 변환한다.

    `attempt`는 이 스텁 자신의 호출 순번이다(`AgentScenario.next_attempt()`가 관리) —
    오케스트레이터의 재시도 카운터(`workflow_tasks.attempt`)와는 이름만 같은 별개의
    숫자다. 두 카운터를 맞추려 하지 않는다: 오케스트레이터가 몇 번째 시도인지와
    무관하게, 이 스텁은 자신이 몇 번째로 호출됐는지만 안다.
    """

    def __init__(self, scenario: AgentScenario, agent: str) -> None:
        self.scenario = scenario
        self.agent = agent

    def build_payload(self, attempt: int | None = None) -> dict:
        """`attempt`를 생략하면 시나리오의 내부 호출 카운터(운영 경로에서 쓰는
        `AgentScenario.next_attempt()`)를 쓴다. 명시적으로 넘기면(단위 테스트
        전용) 그 값이 카운터를 대신하며 카운터 자체는 건드리지 않는다.
        """
        if attempt is None:
            attempt = self.scenario.next_attempt()
        if self.scenario.failure_for_attempt(attempt) == "crash":
            raise StubCrash(f"{self.agent} attempt {attempt} 크래시 주입")
        if self.scenario.latency_ms:
            time.sleep(self.scenario.latency_ms / 1000)

        verdict = self.scenario.next_verdict()
        payload: dict = {"kind": ARTIFACT_KIND[self.agent], "agent": self.agent}
        if verdict is not None:
            # verdict는 주장이 아니라 종료 코드에서 도출한다.
            payload["exit_code"] = 0 if verdict == "PASS" else 1
            payload["verdict"] = "PASS" if payload["exit_code"] == 0 else "FAIL"
        else:
            payload["exit_code"] = 0
        return payload

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """SDK `AgentExecutor` 인터페이스.

        `build_payload`가 던지는 `StubCrash`는 여기서 잡지 않고 그대로
        전파한다 — SDK는 execute()의 처리되지 않은 예외를 태스크의
        TASK_STATE_ERROR로 변환한다(에이전트 프로세스 크래시를 흉내낸다).
        이는 "FAIL 판정"(정상 완료, exit_code != 0)과는 다른 경로다.
        """
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        # attempt를 넘기지 않는다 — 시나리오 자신의 호출 카운터가 운영 경로다.
        payload = self.build_payload()

        await updater.add_artifact([_payload_to_part(payload)], name=payload["kind"])
        if payload["exit_code"] == 0:
            await updater.complete()
        else:
            await updater.failed()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """SDK `AgentExecutor` 인터페이스. 스텁은 취소 요청을 즉시 반영한다."""
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()
