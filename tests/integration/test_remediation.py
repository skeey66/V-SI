"""환류 루프와 반복 상한 통합 테스트.

전제: `docker compose up -d --build`로 스택이 떠 있어야 한다(해피 패스와 동일).

여기서 고정하는 것은 셋이다.

1. 검증이 FAIL이면 새 revision을 열고 dev부터 다시 돌린다 — 그리고 언젠가는
   PASS로 끝난다.
2. 환류는 기존 Task 행을 되돌리지 않는다. 새 행을 만들고 `revision_of`로 직전
   회차를 가리켜 계보를 잇는다.
3. 반복 상한은 워크플로 층에 있다. 에이전트가 영원히 FAIL을 내도 `max_revisions`
   에서 멈추고 `escalated`로 간다 — 무한 루프가 나지 않는다.
"""

from __future__ import annotations

import pytest

from orchestrator.workflow import RequirementState
from tests.integration.harness import run_scenario

#: 브리프는 `scenarios/qa_fails_twice.yaml`을 지정했지만 그 시나리오의 dev에는
#: `attempt_2: crash`가 들어 있다 — Task 7 판정이 정의한 **재시도(Task 12)**용
#: fixture다. 재시도가 없는 지금은 revision 2의 dev가 FAILED로 멈춰 환류 루프가
#: 아니라 재시도의 부재를 시험하게 된다(실측: SDK는 실패 경로에서 푸시를 보내지
#: 않아 오케스트레이터가 크래시를 알지도 못한다). 그래서 verdict 순서는 같고
#: 크래시만 없는 시나리오를 따로 두고 여기서는 환류만 본다.
FAILS_TWICE = "scenarios/verdicts_fail_twice.yaml"
NEVER_PASSES = "scenarios/never_passes.yaml"


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


async def test_two_failed_rounds_then_accepted() -> None:
    result = await run_scenario(FAILS_TWICE, "REQ-010", "회원가입")
    assert result.state is RequirementState.ACCEPTED
    assert result.revision == 3


async def test_revision_lineage_is_linked() -> None:
    result = await run_scenario(FAILS_TWICE, "REQ-011", "회원가입")
    dev_tasks = sorted(
        [t for t in result.tasks if t.agent == "dev"], key=lambda t: t.revision
    )
    assert [t.revision for t in dev_tasks] == [1, 2, 3]
    assert dev_tasks[0].revision_of is None
    assert dev_tasks[1].revision_of == dev_tasks[0].task_id
    assert dev_tasks[2].revision_of == dev_tasks[1].task_id


async def test_limit_exceeded_escalates() -> None:
    result = await run_scenario(NEVER_PASSES, "REQ-012", "회원가입")
    assert result.state is RequirementState.ESCALATED
    assert result.revision == 3

    # 상한은 워크플로 층에 있다: revision 3을 넘어서는 Task가 아예 만들어지지 않는다.
    assert max(t.revision for t in result.tasks) == 3
    # 각 회차마다 dev + qa + security가 한 번씩. planner는 첫 회차에만 돈다.
    assert len([t for t in result.tasks if t.agent == "planner"]) == 1
    assert len([t for t in result.tasks if t.agent == "dev"]) == 3


async def test_remediation_records_revision_started_events() -> None:
    """환류가 아웃박스에 흔적을 남기는지 — 상태만 바뀌고 이벤트가 유실되면 안 된다."""
    result = await run_scenario(NEVER_PASSES, "REQ-013", "회원가입")
    transitions = [
        e.payload["to"] for e in result.events if e.event_type == "state_changed"
    ]
    assert transitions == [
        "planned",
        "implementing",
        "verifying",
        "remediating",
        "implementing",
        "verifying",
        "remediating",
        "implementing",
        "verifying",
        "remediating",
        "escalated",
    ]
    started = [
        e.payload["revision"]
        for e in result.events
        if e.event_type == "revision_started"
    ]
    assert started == [2, 3]
