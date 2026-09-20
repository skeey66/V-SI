"""`run_s`를 요구사항 나이 backstop으로 배선한다 (Task 11, 스펙 §9.2).

브리프의 예시 테스트는 `Action.GIVE_UP`(클래스 속성)을 가정했지만 실제
`Action`은 `kind` 문자열을 담는 데이터클래스다(`orchestrator.reconciler`의
`GIVE_UP` 모듈 상수가 그 kind 값이다) — 아래는 실제 시그니처·타입에 맞춘
버전이다. 더미 요구사항 객체에도 `updated_at`을 채운다 — `next_action`이
`last_activity`에서 그 필드를 읽기 때문이다.
"""
from datetime import datetime, timedelta, timezone

from orchestrator.reconciler import GIVE_UP, next_action


def _req(created_minutes_ago: int):
    created = datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago)

    class R:
        requirement_id = "REQ-1"
        state = "implementing"
        revision = 1
        max_revisions = 3
        created_at = created
        updated_at = created
    return R()


def test_young_requirement_is_not_given_up() -> None:
    action = next_action(
        _req(5), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=5.0, stuck_after_s=900.0, run_s=3600.0,
    )
    assert action is None or action.kind != GIVE_UP


def test_requirement_past_run_budget_is_given_up() -> None:
    """스펙 §9.2 — run_s 를 넘기면 더 묻지 않는다."""
    action = next_action(
        _req(61), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=5.0, stuck_after_s=900.0, run_s=3600.0,
    )
    assert action is not None and action.kind == GIVE_UP


def test_run_budget_wins_over_other_actions() -> None:
    """예산 초과는 다른 어떤 조치보다 우선한다 — 무한 진행을 막는 backstop 이다."""
    action = next_action(
        _req(120), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=5.0, stuck_after_s=900.0, run_s=3600.0,
    )
    assert action is not None and action.kind == GIVE_UP
