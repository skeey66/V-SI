"""해피 패스 통합 테스트.

전제: `docker compose up -d --build`로 스택(postgres + 에이전트 4종 +
orchestrator)이 떠 있어야 한다. 하네스가 에이전트만 시나리오에 맞춰
재기동하고, 오케스트레이터와 postgres는 그대로 쓴다.
"""

from __future__ import annotations

import pytest

from orchestrator.workflow import RequirementState
from tests.integration.harness import run_scenario

SCENARIO = "scenarios/all_pass.yaml"


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


async def test_all_pass_reaches_accepted_without_revision() -> None:
    result = await run_scenario(SCENARIO, "REQ-001", "회원가입")
    assert result.state is RequirementState.ACCEPTED
    assert result.revision == 1
    assert result.task_count == 4  # planner + dev + qa + security


async def test_events_recorded_for_every_transition() -> None:
    result = await run_scenario(SCENARIO, "REQ-002", "로그인")
    types = [e.event_type for e in result.events]
    assert "state_changed" in types
    assert all(e.published_at is None for e in result.events)

    # planned → implementing → verifying → accepted: 상태 전이 4회가 모두 남는다.
    transitions = [
        e.payload["to"] for e in result.events if e.event_type == "state_changed"
    ]
    assert transitions == ["planned", "implementing", "verifying", "accepted"]


async def test_tasks_carry_verdicts_and_distinct_idempotency_keys() -> None:
    """Task 11·12가 소비하는 필드들이 실제로 채워졌는지 고정한다."""
    result = await run_scenario(SCENARIO, "REQ-003", "비밀번호 재설정")

    by_agent = {t.agent: t for t in result.tasks}
    assert set(by_agent) == {"planner", "dev", "qa", "security"}
    assert all(t.state == "completed" for t in result.tasks)
    assert all(t.a2a_task_id for t in result.tasks)
    assert all(t.revision == 1 and t.attempt == 1 for t in result.tasks)
    assert all(t.revision_of is None for t in result.tasks)

    # verdict는 검증 에이전트에서만 나오고, 종료 코드에서 도출된다.
    assert by_agent["qa"].verdict == "PASS"
    assert by_agent["security"].verdict == "PASS"
    assert by_agent["planner"].verdict is None
    assert by_agent["dev"].verdict is None

    keys = {t.idempotency_key for t in result.tasks}
    assert len(keys) == 4

    kinds = {a.kind for a in result.artifacts}
    assert kinds == {"requirements", "source_code", "test_report", "security_report"}
