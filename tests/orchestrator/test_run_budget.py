"""`run_s`를 요구사항 나이 backstop으로 배선한다 (Task 11, 스펙 §9.2).

브리프의 예시 테스트는 `Action.GIVE_UP`(클래스 속성)을 가정했지만 실제
`Action`은 `kind` 문자열을 담는 데이터클래스다(`orchestrator.reconciler`의
`GIVE_UP` 모듈 상수가 그 kind 값이다) — 아래는 실제 시그니처·타입에 맞춘
버전이다. 더미 요구사항 객체에도 `updated_at`을 채운다 — `next_action`이
`last_activity`에서 그 필드를 읽기 때문이다.

리뷰 라운드 1: 처음 구현은 이 검사를 함수 맨 앞(다른 어떤 판단보다 먼저)에
두었다 — 브리프가 그렇게 시켰다. 하지만 그러면 마지막 검증자가 이미 PASS를
써서 FINISH를 돌려줘야 할 요구사항도, 나이가 run_s를 넘기기만 하면 GIVE_UP을
받는다. **예산은 새 일을 막을 뿐, 이미 끝난 일을 버리지 않는다** — 그래서
아래 `test_finished_verification_survives_run_budget` 등으로 "이미 끝난
일(FINISH/ADVANCE)과 SDK 결함 안전망(FORCE_FAIL)은 건드리지 않고, 새 일을
만드는 결정(DISPATCH/PROBE/REMEDIATE)만 GIVE_UP으로 바꿔치기한다"는 순서를
고정한다. 실제 DB 부수효과(비어 있는 `tasks`로도 요구사항이 실제로 ESCALATED
로 전이되는지)는 `tests/orchestrator/test_operational_signals.py`가 별도로
고정한다 — 여기는 `next_action`의 순수 판단만 본다.
"""
from datetime import datetime, timedelta, timezone

from orchestrator.models import WorkflowTask
from orchestrator.reconciler import ADVANCE, FINISH, FORCE_FAIL, GIVE_UP, next_action

STALE_AFTER_S = 5.0
STUCK_AFTER_S = 900.0
RUN_S = 3600.0


def _req(created_minutes_ago: int, req_state: str = "implementing"):
    created = datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago)

    class R:
        requirement_id = "REQ-1"
        state = req_state
        revision = 1
        max_revisions = 3
        created_at = created
        updated_at = created
    return R()


def _task(agent: str, state: str, *, age_s: float = 0.0, verdict: str | None = None) -> WorkflowTask:
    now = datetime.now(timezone.utc)
    return WorkflowTask(
        task_id=f"task-{agent}-{state}-{age_s}",
        requirement_id="REQ-1",
        agent=agent,
        revision=1,
        a2a_task_id=f"a2a-{agent}",
        idempotency_key=f"key-{agent}-{state}-{age_s}",
        state=state,
        verdict=verdict,
        attempt=1,
        created_at=now - timedelta(seconds=age_s),
        completed_at=now - timedelta(seconds=age_s) if state == "completed" else None,
    )


def test_young_requirement_is_not_given_up() -> None:
    action = next_action(
        _req(5), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is None or action.kind != GIVE_UP


def test_requirement_past_run_budget_is_given_up() -> None:
    """스펙 §9.2 — run_s 를 넘기면 더 묻지 않는다(새 일을 만들지 않는다)."""
    action = next_action(
        _req(61), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == GIVE_UP


def test_run_budget_preempts_dispatch() -> None:
    """열린 행이 없는데 아직 done도 아니면 DISPATCH할 참이었다 — 예산이 막는다."""
    action = next_action(
        _req(120), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == GIVE_UP


def test_run_budget_preempts_probe() -> None:
    """리뷰 라운드 1 — 열린 행이 아직 stuck은 아니어도(PROBE 대상) 예산을
    넘겼으면 더 묻지 않는다. PROBE는 "더 기다린다"는 뜻이라 새 일에 준한다."""
    now = datetime.now(timezone.utc)
    row = _task("dev", "working", age_s=10.0)  # STUCK_AFTER_S 안쪽 — PROBE 대상이지 FORCE_FAIL 대상이 아니다.
    action = next_action(
        _req(120), rows=[row], now=now,
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == GIVE_UP


def test_run_budget_preempts_remediate() -> None:
    """REMEDIATING도 새 revision·새 dev Task를 만드는 결정이라 예산 대상이다.

    (실행부 회귀 — Task 11 리뷰 라운드 1) 이 시나리오는 열린 행도 완료 행도
    전혀 없다(예: `remediate` 두 트랜잭션 사이에서 죽어 이번 회차 Task가 0개인
    채로 시간을 다 쓴 경우). `next_action`은 여전히 GIVE_UP(빈 tasks)을
    돌려주는 게 맞다 — 그 GIVE_UP을 실제로 집행하는지(비어 있는 `tasks`로도
    `Reconciler._execute`가 요구사항을 진짜로 ESCALATED시키는지)는 순수 함수
    바깥의 일이라 `test_operational_signals.py`가 실제 DB로 고정한다.
    """
    action = next_action(
        _req(120, req_state="remediating"), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == GIVE_UP
    assert action.tasks == ()


def test_finished_verification_survives_run_budget() -> None:
    """FINISH는 이미 끝난 일이다 — 예산 초과라도 버리지 않는다 (리뷰 라운드 1)."""
    # age_s를 stale_after_s(5초)보다 훨씬 크게 잡는다 — 그래야 "방금 끝났다"는
    # freshness 가드에 걸려 next_action이 조기에 None을 돌려주는 일 없이
    # FINISH 분기까지 실제로 도달한다.
    rows = [
        _task("qa", "completed", age_s=60.0, verdict="PASS"),
        _task("security", "completed", age_s=60.0, verdict="PASS"),
    ]
    action = next_action(
        _req(61, req_state="verifying"), rows=rows, now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == FINISH


def test_advance_survives_run_budget() -> None:
    """ADVANCE도 이미 끝난 일(Task 완료)의 뒷정리다 — 버리지 않는다."""
    rows = [_task("dev", "completed", age_s=60.0)]
    action = next_action(
        _req(61, req_state="implementing"), rows=rows, now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == ADVANCE


def test_force_fail_survives_run_budget() -> None:
    """SDK 결함 안전망(FORCE_FAIL)은 예산보다 먼저 본다 — 갇힌 행부터 정리한다."""
    rows = [_task("dev", "working", age_s=STUCK_AFTER_S + 10.0)]
    action = next_action(
        _req(120, req_state="implementing"), rows=rows, now=datetime.now(timezone.utc),
        stale_after_s=STALE_AFTER_S, stuck_after_s=STUCK_AFTER_S, run_s=RUN_S,
    )
    assert action is not None and action.kind == FORCE_FAIL
