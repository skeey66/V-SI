"""리컨실러의 **판단**을 DB·컨테이너 없이 고정한다.

`next_action`은 순수 함수다 — (요구사항 행, Task 행들, 현재 시각)만 보고 다음에
할 일 하나를 고른다. 이벤트를 재생하지 않고 현재 상태만 관찰한다는 성질이
"입력이 곧 전부"라는 형태로 드러나므로, 복구 케이스 네 가지를 밀리초 단위로
결정적으로 시험할 수 있다. 실제 수렴(에이전트 왕복 포함)은
`tests/integration/test_reconcile.py`가 본다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orchestrator.reconciler import (
    ADVANCE,
    DISPATCH,
    FINISH,
    GIVE_UP,
    PROBE,
    REMEDIATE,
    next_action,
)
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.workflow import RequirementState

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
STALE_AFTER_S = 5.0
OLD = 600.0  # 충분히 오래된 나이(초)


def _req(state: RequirementState, revision: int = 1, age_s: float = OLD):
    return WorkflowRequirement(
        requirement_id="REQ-X",
        title="회원가입",
        state=state.value,
        revision=revision,
        max_revisions=3,
        run_id="run-x",
        created_at=NOW - timedelta(seconds=age_s),
        updated_at=NOW - timedelta(seconds=age_s),
    )


def _task(
    agent: str,
    state: str,
    revision: int = 1,
    age_s: float = OLD,
    a2a_task_id: str | None = "a2a-1",
    verdict: str | None = None,
    attempt: int = 1,
    failure_class: str | None = None,
):
    return WorkflowTask(
        task_id=f"task-{agent}-{revision}-{state}-{attempt}",
        requirement_id="REQ-X",
        agent=agent,
        revision=revision,
        a2a_task_id=a2a_task_id,
        idempotency_key=f"key-{agent}-{revision}-{state}-{attempt}",
        state=state,
        verdict=verdict,
        attempt=attempt,
        failure_class=failure_class,
        created_at=NOW - timedelta(seconds=age_s),
        completed_at=NOW - timedelta(seconds=age_s) if state == "completed" else None,
    )


def _plan(req, rows):
    return next_action(req, rows, NOW, STALE_AFTER_S)


# --------------------------------------------------------- 케이스 1: 유령 working


def test_stale_working_row_is_probed() -> None:
    """executor가 크래시하면 푸시가 오지 않는다 — 오래 머문 행을 권위 있게 읽는다."""
    req = _req(RequirementState.IMPLEMENTING)
    stuck = _task("dev", "working")
    action = _plan(req, [_task("planner", "completed"), stuck])
    assert action.kind == PROBE
    assert [t.task_id for t in action.tasks] == [stuck.task_id]


def test_fresh_working_row_is_left_alone() -> None:
    """진행 중인 작업에는 손대지 않는다 — 이것이 리컨실러의 첫 번째 안전장치다."""
    req = _req(RequirementState.IMPLEMENTING)
    fresh = _task("dev", "working", age_s=1.0)
    assert _plan(req, [_task("planner", "completed"), fresh]) is None


def test_failed_row_is_superseded_by_a_new_dispatch() -> None:
    """Task 행은 불변이다 — 죽은 행을 되살리지 않고 같은 에이전트를 새로 디스패치한다."""
    req = _req(RequirementState.IMPLEMENTING)
    rows = [_task("planner", "completed"), _task("dev", "failed")]
    assert _plan(req, rows) == _dispatch("dev")


# ------------------------- 케이스 2: REMEDIATING + 올라간 revision + 태스크 0개


def test_remediating_with_bumped_revision_and_no_tasks_is_resumed() -> None:
    """`remediate`가 두 트랜잭션이라 사이에서 죽으면 이 조합이 남는다.

    revision은 2인데 revision 2의 Task가 하나도 없고 상태는 remediating이다.
    푸시도 완료 알림도 영영 오지 않으므로 현재 상태 관찰만이 유일한 단서다.
    """
    req = _req(RequirementState.REMEDIATING, revision=2)
    rows = [
        _task("planner", "completed", revision=1),
        _task("dev", "completed", revision=1),
        _task("qa", "completed", revision=1, verdict="FAIL"),
        _task("security", "completed", revision=1, verdict="FAIL"),
    ]
    assert _plan(req, rows).kind == REMEDIATE


def test_remediating_before_revision_bump_is_also_resumed() -> None:
    """판정 트랜잭션 직후에 죽은 경우 — revision은 아직 그대로다. 같은 동작으로 수렴한다."""
    req = _req(RequirementState.REMEDIATING, revision=1)
    rows = [
        _task("dev", "completed"),
        _task("qa", "completed", verdict="FAIL"),
        _task("security", "completed", verdict="FAIL"),
    ]
    assert _plan(req, rows).kind == REMEDIATE


# ------------------------------------- 케이스 3: submitted + a2a_task_id NULL


def test_submitted_without_agent_task_id_is_probed() -> None:
    """`submit()` 자체가 터진 경우 — 에이전트 쪽에 물어볼 대상조차 없다."""
    req = _req(RequirementState.PLANNED)
    orphan = _task("planner", "submitted", a2a_task_id=None)
    action = _plan(req, [orphan])
    assert action.kind == PROBE
    assert [t.a2a_task_id for t in action.tasks] == [None]


# ------------------------------------------- 상태가 Task 진행보다 뒤처진 경우


def test_planner_done_but_state_not_advanced() -> None:
    req = _req(RequirementState.PLANNED)
    assert _plan(req, [_task("planner", "completed")]) == _advance("planner")


def test_dev_done_but_verifiers_never_dispatched() -> None:
    req = _req(RequirementState.IMPLEMENTING)
    rows = [_task("planner", "completed"), _task("dev", "completed")]
    assert _plan(req, rows) == _advance("dev")


def test_verifying_with_one_verifier_missing_dispatches_it() -> None:
    req = _req(RequirementState.VERIFYING)
    rows = [
        _task("planner", "completed"),
        _task("dev", "completed"),
        _task("qa", "completed", verdict="PASS"),
    ]
    assert _plan(req, rows) == _dispatch("security")


def test_verifying_with_both_verdicts_asks_for_judgement() -> None:
    """마지막 완료 직후에 죽으면 판정이 유실된다 — verifying에 영원히 남는다."""
    req = _req(RequirementState.VERIFYING)
    rows = [
        _task("dev", "completed"),
        _task("qa", "completed", verdict="PASS"),
        _task("security", "completed", verdict="PASS"),
    ]
    assert _plan(req, rows).kind == FINISH


def test_empty_requirement_dispatches_planner() -> None:
    assert _plan(_req(RequirementState.PLANNED), []) == _dispatch("planner")


def test_terminal_requirement_is_never_touched() -> None:
    for state in (RequirementState.ACCEPTED, RequirementState.ESCALATED):
        assert _plan(_req(state), [_task("dev", "working")]) is None


def test_fresh_requirement_without_tasks_is_left_to_the_dispatcher() -> None:
    """방금 만들어진 요구사항은 아직 디스패치 중일 수 있다 — 끼어들지 않는다."""
    req = _req(RequirementState.PLANNED, age_s=0.5)
    assert next_action(req, [], NOW, STALE_AFTER_S) is None


def test_previous_revision_rows_do_not_count_as_progress() -> None:
    """지난 회차의 완료 행은 이번 회차의 근거가 아니다."""
    req = _req(RequirementState.IMPLEMENTING, revision=2)
    rows = [_task("planner", "completed", revision=1), _task("dev", "completed", revision=1)]
    assert _plan(req, rows) == _dispatch("dev")


# -------------------------------------------- 재시도 상한(캡): 영원히 크래시하는 에이전트


def test_single_crash_is_still_within_budget() -> None:
    """실패가 한 번뿐이면(EXECUTION 상한 2) 아직 예산이 남아 있다 — 계속 재시도한다.

    `test_failed_row_is_superseded_by_a_new_dispatch`와 같은 사실을 캡의
    관점에서 다시 고정한다: 캡이 생겼다고 정당한 재시도(Task 13의 기존 동작)가
    막히면 안 된다.
    """
    req = _req(RequirementState.IMPLEMENTING)
    rows = [
        _task("planner", "completed"),
        _task("dev", "failed", failure_class="execution", attempt=1),
    ]
    assert _plan(req, rows) == _dispatch("dev")


def test_repeated_execution_crashes_exhaust_budget_and_give_up() -> None:
    """EXECUTION 상한은 2 — 두 번째 크래시에서 예산이 바닥나 포기한다.

    영원히 크래시하는 에이전트를 리컨실러가 끝없이 재디스패치하지 않는다는
    계약이다(Task 12). Task 행은 그대로 두고(불변) 더 이상 새 행을 만들지 않는다.
    """
    req = _req(RequirementState.IMPLEMENTING)
    rows = [
        _task("planner", "completed"),
        _task("dev", "failed", failure_class="execution", attempt=1),
        _task("dev", "failed", failure_class="execution", attempt=2),
    ]
    assert _plan(req, rows) == _give_up("dev")


def test_transport_budget_is_larger_than_execution() -> None:
    """TRANSPORT 상한은 3 — EXECUTION이면 포기했을 실패 횟수에서도 아직 재시도한다."""
    req = _req(RequirementState.IMPLEMENTING)
    rows = [
        _task("planner", "completed"),
        _task("dev", "failed", failure_class="transport", attempt=1),
        _task("dev", "failed", failure_class="transport", attempt=2),
    ]
    assert _plan(req, rows) == _dispatch("dev")


def test_poison_never_gets_a_second_try() -> None:
    """POISON 상한은 1 — 첫 실패에서 바로 포기한다(재시도해도 결정적으로 같다)."""
    req = _req(RequirementState.PLANNED)
    rows = [_task("planner", "failed", failure_class="poison", attempt=1)]
    assert _plan(req, rows) == _give_up("planner")


def test_unclassified_failure_falls_back_to_execution_budget() -> None:
    """`failure_class`가 비어 있던 옛 실패 행도 캡이 있어야 한다 — EXECUTION으로 본다."""
    req = _req(RequirementState.PLANNED)
    rows = [
        _task("planner", "failed", failure_class=None, attempt=1),
        _task("planner", "failed", failure_class=None, attempt=2),
    ]
    assert _plan(req, rows) == _give_up("planner")


def test_exhausted_verifier_gives_up_during_verifying() -> None:
    """검증 에이전트도 같은 캡을 받는다 — verifying 도중에도 영원히 돌지 않는다."""
    req = _req(RequirementState.VERIFYING)
    rows = [
        _task("planner", "completed"),
        _task("dev", "completed"),
        _task("qa", "completed", verdict="PASS"),
        _task("security", "failed", failure_class="execution", attempt=1),
        _task("security", "failed", failure_class="execution", attempt=2),
    ]
    assert _plan(req, rows) == _give_up("security")


def _dispatch(*agents: str):
    from orchestrator.reconciler import Action

    return Action(DISPATCH, tuple(agents))


def _advance(agent: str):
    from orchestrator.reconciler import Action

    return Action(ADVANCE, (agent,))


def _give_up(*agents: str):
    from orchestrator.reconciler import Action

    return Action(GIVE_UP, tuple(agents))
