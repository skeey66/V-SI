"""해피 패스 통합 테스트.

전제: `docker compose up -d --build`로 스택(postgres + 에이전트 4종 +
orchestrator)이 떠 있어야 한다. 하네스가 에이전트만 시나리오에 맞춰
재기동하고, 오케스트레이터와 postgres는 그대로 쓴다.
"""

from __future__ import annotations

import httpx
import pytest

from orchestrator.workflow import RequirementState
from tests.integration.harness import ORCHESTRATOR_URL, run_scenario

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
    """오케스트레이터 쪽 아웃박스 계약만 고정한다: 상태 전이·Task 착수·Task
    완료마다 이벤트 행 하나를 기록한다(어떤 종류로, 어떤 aggregate로, 어떤
    순서로).

    리뷰 라운드 1 이전 버전은 `all(e.published_at is None for e in
    result.events)`를 같이 확인했는데, 그건 오케스트레이터의 불변식이 아니라
    게이트웨이의 불변식이었다 — 게이트웨이는 요구사항별 구독 필터 없이 전역
    브로드캐스트하므로, 이 시험이 도는 순간 다른 WebSocket 클라이언트(그래프
    UI 등)가 붙어 있으면 그 클라이언트에게 실제로 전달되고 정상적으로
    `published_at`이 찍힌다 — 그게 게이트웨이의 설계대로 동작하는 것이다.
    그 assert는 "아무도 안 보고 있다"는 우연에 기대 통과해 왔을 뿐이고, 실제로
    그래프 UI를 켜 둔 채로 이 스위트를 돌리면 깨졌다(Task 17에서 확인).
    "새로 쓴 행은 미발행 상태로 커밋된다"는 쓰기 쪽 계약은
    `tests/orchestrator/test_outbox.py::test_state_change_and_event_commit_together`
    가 이미 살아 있는 게이트웨이 없이(로컬 스키마, 클라이언트 없음) 고정하고
    있다 — 라이브 실행 뒤에 다시 확인할 이유가 없다.
    """
    result = await run_scenario(SCENARIO, "REQ-002", "로그인")

    # planned → implementing → verifying → accepted: 상태 전이 4회가, 정확히
    # 이 요구사항을 가리키는(aggregate="requirement", aggregate_id=REQ-002)
    # state_changed 이벤트로, event_id 오름차순 그대로 남는다.
    state_changed = [e for e in result.events if e.event_type == "state_changed"]
    assert len(state_changed) == 4
    assert all(e.aggregate == "requirement" and e.aggregate_id == "REQ-002" for e in state_changed)
    assert [e.payload["to"] for e in state_changed] == [
        "planned", "implementing", "verifying", "accepted",
    ]

    # task_submitted는 오케스트레이터가 순차적으로(await dispatch_agent(...))
    # 디스패치하므로 event_id 순서가 planner→dev→qa→security로 결정적이다.
    submitted = [e for e in result.events if e.event_type == "task_submitted"]
    assert [e.payload["agent"] for e in submitted] == ["planner", "dev", "qa", "security"]

    # task_completed도 넷 다 나오지만, qa·security는 서로 독립된 프로세스가
    # 각자 푸시를 보내는 경쟁 관계라 둘 사이의 완료 순서까지 결정적이지는
    # 않다 — planner→dev 구간만 인과적으로 순서가 보장된다(dev는 planner의
    # 완료 신호를 받아야 디스패치되므로).
    completed = [e for e in result.events if e.event_type == "task_completed"]
    completed_agents = [e.payload["agent"] for e in completed]
    assert completed_agents[:2] == ["planner", "dev"]
    assert set(completed_agents[2:]) == {"qa", "security"}


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


async def test_reposting_the_same_requirement_is_409_not_500() -> None:
    """데모의 정문이 같은 명령을 두 번 받으면 409를 돌려준다.

    `scripts/demo.sh`를 두 번 치는 것은 흔한 일이다. 유니크 제약 위반이
    `IntegrityError` 그대로 새어 나가 500이 되면, 운영자는 "요구사항을 두 번
    넣었다"를 "오케스트레이터가 고장났다"로 읽는다.
    """
    result = await run_scenario(SCENARIO, "REQ-004", "약관 동의")
    assert result.state is RequirementState.ACCEPTED

    async with httpx.AsyncClient(timeout=30) as c:
        again = await c.post(
            f"{ORCHESTRATOR_URL}/requirements",
            json={
                "requirement_id": "REQ-004",
                "title": "약관 동의",
                "run_id": "run-REQ-004-again",
            },
        )
    assert again.status_code == 409, again.text
