"""실제 모델로 도는 소수의 테스트 (스펙 §12.4, §13).

**모델 출력 내용은 절대 단언하지 않는다.** 단언하는 것은 성질이다 — 종단
상태에 도달했는가, 판정이 도구 종료코드와 일치하는가(모델이 뭐라고 말하든),
에이전트가 실제로 도구를 불렀는가, 환류가 실제 실패 출력을 실어 날랐는가.
생성된 코드의 품질은 이 파일이 검증할 대상이 아니다 — `accepted`도
`escalated`도 둘 다 "사람 개입 없이 종단 상태에 도달했다"는 같은 성공이다
(스펙 §1, §13 기준 1).

**전제(사람이 미리 해 둔다 — 이 테스트는 스택을 띄우지도, 재기동하지도 않는다)**:

```bash
ollama serve &
ollama pull qwen3:8b
VSI_AGENT_MODE=llm docker compose up -d --force-recreate planner dev qa security
```

**왜 `harness.run_scenario`를 안 쓰는가**: 그 함수의 첫 인자는 시나리오 yaml
경로이고 `None`을 받지 않는다 — 내부에서 `reset_agents(scenario_path)`가
에이전트 4종을 강제 재기동해 스텁 에이전트의 verdict 커서를 초기화하기
때문이다. LLM 에이전트에는 그런 커서가 없고 `VSI_SCENARIO`도 읽지 않으니,
매 테스트마다 컨테이너를 내렸다 올리는 비용(분 단위로 걸릴 수 있다, 이미
느린 실제 모델 호출 위에 얹기엔 낭비다)만 남는다. 대신
`harness.run_scenario_live`를 쓴다 — 정리·제출·폴링 로직은 그대로 재사용하고
재기동 단계만 뺀, 이 파일 전용 드라이버다.

**타임아웃**: 실측(2026-09 로컬 `qwen3:8b`, 환류 2회·회차 3개로 accepted)은
11분 30초(690초)였다. `harness.LLM_TERMINAL_TIMEOUT_S`(기본 1200초)를 그대로
쓴다 — 스텁용 상수(90초)는 여기 안 맞는다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from orchestrator.models import OutboxEvent, WorkflowTask
from orchestrator.workflow import RequirementState
from tests.integration.harness import db_session, run_scenario_live

pytestmark = pytest.mark.llm

TERMINAL = {RequirementState.ACCEPTED, RequirementState.ESCALATED}


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


async def test_requirement_reaches_a_terminal_state_without_intervention() -> None:
    """완료 기준 1 — accepted 와 escalated 모두 성공이다 (스펙 §13)."""
    result = await run_scenario_live("REQ-LLM-1", "정수 두 개를 더하는 add(a, b) 함수")
    assert result.state in TERMINAL


async def test_verifier_verdicts_match_their_tool_exit_codes() -> None:
    """완료 기준 3 — LLM 이 판정을 뒤집지 못한다 (스펙 §5.3, §13).

    모델이 대화에서 뭐라고 주장했든, `workflow_tasks.verdict`는 오케스트레이터가
    `run_tests`/`run_security_scan`의 `exit_code`에서 도출한 값이어야 한다
    (`engine.on_task_completed`). `tool_result` 이벤트에 실린 `exit_code`가
    바로 그 근거다.
    """
    await run_scenario_live("REQ-LLM-2", "정수 두 개를 더하는 add(a, b) 함수")
    async with db_session() as s:
        tasks = (
            await s.execute(
                select(WorkflowTask).where(
                    WorkflowTask.requirement_id == "REQ-LLM-2",
                    WorkflowTask.agent.in_(("qa", "security")),
                    WorkflowTask.state == "completed",
                )
            )
        ).scalars().all()
        events = (
            await s.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == "REQ-LLM-2",
                    OutboxEvent.event_type == "tool_result",
                )
            )
        ).scalars().all()

    assert tasks, "검증자 작업이 하나도 완료되지 않았다"
    exit_codes = {
        (e.payload["agent"], e.payload["revision"]): e.payload.get("exit_code")
        for e in events
        if e.payload.get("exit_code") is not None
    }
    for task in tasks:
        code = exit_codes.get((task.agent, task.revision))
        if code is None:
            continue
        assert task.verdict == ("PASS" if code == 0 else "FAIL"), (
            f"{task.agent} rev{task.revision}: verdict={task.verdict} 인데 exit_code={code}"
        )


async def test_agents_actually_called_tools() -> None:
    """도구를 부르지 않은 산출물은 근거가 없다 (스펙 §5.3, §12.4)."""
    await run_scenario_live("REQ-LLM-3", "정수 두 개를 더하는 add(a, b) 함수")
    async with db_session() as s:
        events = (
            await s.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == "REQ-LLM-3",
                    OutboxEvent.event_type == "tool_result",
                )
            )
        ).scalars().all()
    assert events, "도구 호출 이벤트가 하나도 없다"
    assert {e.payload["agent"] for e in events} >= {"planner", "dev"}


async def test_feedback_carries_real_failure_text() -> None:
    """완료 기준 4 — 환류가 실제 실패 출력에 근거한다 (스펙 §4.3, §12.4, §13).

    dev에게 나가는 두 번째 회차 디스패치 페이로드(`feedback`) 자체는 A2A
    메시지로만 나가고 DB에 남지 않는다 — 다만 그 페이로드는
    `engine.build_feedback`이 검증자 아티팩트의 `summary`에서 결정적으로
    만든다(그 변환 자체는 3층 가짜-LLM 테스트가 이미 고정한다). 여기서 실제
    모델로만 확인할 수 있는 것은 재료 쪽이다 — FAIL 판정을 받은 검증자
    아티팩트의 `summary`가 실제 진단 내용을 담은, 속이 빈 문자열이 아닌
    텍스트였는가.

    회차 1에서 바로 승인되면(환류가 아예 일어나지 않으면) 이 성질은 관측할
    자료가 없다 — 실패가 아니라 스킵으로 처리한다. `accepted`(회차 1)도
    §13 기준 1의 성공이기 때문이다.
    """
    result = await run_scenario_live("REQ-LLM-4", "정수 두 개를 더하는 add(a, b) 함수")
    if result.revision <= 1:
        pytest.skip("회차 1에서 바로 승인/이관됐다 — 환류가 발생하지 않았다")

    fails = [
        a.content for a in result.artifacts
        if a.content.get("verdict") == "FAIL"
    ]
    assert fails, "revision > 1 인데 FAIL 판정 아티팩트가 하나도 없다"
    for content in fails:
        summary = (content.get("summary") or "").strip()
        assert summary, f"{content.get('agent')} FAIL 아티팩트에 근거 summary 가 비어 있다"
        assert len(summary) > 10, (
            f"{content.get('agent')} summary 가 실제 진단이라기엔 너무 짧다: {summary!r}"
        )
