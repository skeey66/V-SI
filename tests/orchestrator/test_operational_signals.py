"""운영 신호 세 개가 **실제로 방출되는지** 고정한다.

Task 14가 넣은 세 신호는 지금까지 Jaeger를 손으로 열어야만 확인할 수 있었다.
그런데 이 셋은 장식이 아니라 **측정 도구**다:

- `vsi.probe.repetition` — 같은 행을 몇 번째 PROBE하는가. a2a-sdk 1.1.2의 종료
  전이 누락 버그는 이 값이 계속 올라가는 것으로만 드러난다.
- `vsi.task.open_age_s` (FORCE_FAIL 시점) — `stuck_after_s` 기본값 60초는 아직
  **측정된 값이 아니라 추측값**이다. SP2에서 실제 LLM 지연이 붙기 전에 이 분포를
  쌓아야 근거를 갖고 조정할 수 있다.
- `vsi.transition_skip_count` — 스킵 1회는 정상적인 자기 치유(경합 후 재관찰)지만
  반복되는 스킵은 수렴이 아니라 **정체**다. 둘을 가르는 유일한 신호다.

테스트가 없으면 리팩터가 이 셋을 조용히 떨어뜨리고, 떨어진 사실은 SP2에서
`stuck_after_s`를 조정하려 할 때에야 드러난다(그때는 데이터가 없다).

**전역 TracerProvider를 건드리지 않는다.** `trace.set_tracer_provider`는 한 번만
먹히므로(두 번째부터는 경고만 남기고 무시된다) 여기서 전역을 세우면 실행 순서에
따라 `tests/agent_runtime/test_telemetry.py`가 자기 provider를 못 심고 조용히
깨진다(리뷰 라운드 1에서 실측된 사고와 같은 종류다). 대신 리컨실러 모듈의
`tracer`만 갈아 끼운다 — 엔진의 `trace.get_current_span()`은 그 tracer가 연
span을 컨텍스트에서 그대로 집어 오므로 전역과 무관하게 동작한다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator import reconciler as reconciler_mod
from orchestrator.db import make_engine
from orchestrator.engine import WorkflowEngine
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.reconciler import ADVANCE, FORCE_FAIL, GIVE_UP, PROBE, Action, Reconciler
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


@pytest.fixture
def spans(monkeypatch) -> InMemorySpanExporter:
    """리컨실러가 여는 span을 인메모리로 받아 본다(전역 provider 불변)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(reconciler_mod, "tracer", provider.get_tracer("test"))
    return exporter


def _attrs(exporter: InMemorySpanExporter, name: str) -> dict:
    """이름이 `name`인 span의 속성. 없으면 무엇이 있었는지 알려 주고 실패한다."""
    found = [s for s in exporter.get_finished_spans() if s.name == name]
    assert found, (
        f"span '{name}'이 방출되지 않았다. 관측된 span: "
        f"{[s.name for s in exporter.get_finished_spans()]}"
    )
    return dict(found[0].attributes)


class _StubEngine:
    """엔진 호출을 삼키기만 한다 — span을 여는 쪽은 리컨실러다."""

    def __init__(self) -> None:
        self.refreshed: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []

    async def refresh_task(self, agent: str, a2a_task_id: str) -> str:
        self.refreshed.append((agent, a2a_task_id))
        return "working"

    async def on_task_failed(self, task_id: str, observed: str, failure_class) -> None:
        self.failed.append((task_id, observed))


def _open_row(task_id: str, age_s: float, now: datetime) -> WorkflowTask:
    """DB에 붙지 않은 열린 Task 행 하나(순수 관측 대상)."""
    return WorkflowTask(
        task_id=task_id,
        requirement_id="REQ-S-1",
        agent="dev",
        revision=1,
        a2a_task_id=f"a2a-{task_id}",
        idempotency_key=f"k-{task_id}",
        state="working",
        attempt=1,
        created_at=now - timedelta(seconds=age_s),
    )


async def test_probe_emits_repetition_and_open_age(spans) -> None:
    """PROBE는 반복 횟수와 행 나이를 span 속성으로 남긴다.

    같은 행을 두 번 PROBE하면 반복 횟수가 1 → 2로 올라가야 한다 — 그 증가가
    "PROBE가 비정상적으로 반복된다"를 로그 없이 드러내는 유일한 신호다.
    """
    now = datetime.now(timezone.utc)
    engine = _StubEngine()
    rec = Reconciler(session_maker=None, engine=engine, stuck_after_s=600.0)
    action = Action(PROBE, tasks=(_open_row("task-1", age_s=12.5, now=now),))

    await rec._execute("REQ-S-1", action, now)
    attrs = _attrs(spans, "reconciler.probe_task")
    assert attrs["vsi.probe.repetition"] == 1
    assert attrs["vsi.task_id"] == "task-1"
    assert attrs["vsi.agent"] == "dev"
    assert attrs["vsi.task.open_age_s"] == pytest.approx(12.5, abs=0.5)

    # 같은 행을 다시 PROBE하면 반복 횟수만 올라간다.
    spans.clear()
    await rec._execute("REQ-S-1", action, now)
    assert _attrs(spans, "reconciler.probe_task")["vsi.probe.repetition"] == 2

    assert engine.refreshed == [("dev", "a2a-task-1"), ("dev", "a2a-task-1")]


async def test_force_fail_emits_open_age_and_the_ceiling_it_crossed(spans) -> None:
    """FORCE_FAIL은 발동 순간의 행 나이와 그때의 천장을 함께 남긴다.

    `stuck_after_s`를 나중에 근거 있게 조정하려면 "몇 초짜리 행이 어떤 천장에
    걸렸는가"가 쌍으로 있어야 한다 — 나이만 있으면 천장이 바뀐 뒤의 데이터와
    섞이고, 천장만 있으면 조정할 방향을 알 수 없다.
    """
    now = datetime.now(timezone.utc)
    engine = _StubEngine()
    rec = Reconciler(session_maker=None, engine=engine, stuck_after_s=60.0)
    action = Action(FORCE_FAIL, tasks=(_open_row("task-2", age_s=95.0, now=now),))

    await rec._execute("REQ-S-1", action, now)

    attrs = _attrs(spans, "reconciler.force_fail")
    assert attrs["vsi.task_id"] == "task-2"
    assert attrs["vsi.task.open_age_s"] == pytest.approx(95.0, abs=0.5)
    assert attrs["vsi.stuck_after_s"] == 60.0
    assert engine.failed == [("task-2", "stuck_beyond_ceiling")]


async def test_transition_skip_emits_a_counting_event(spans, session) -> None:
    """전이 스킵은 **누적 횟수**를 실은 span 이벤트를 남긴다.

    리컨실러가 요구사항 행의 잠금을 놓은 뒤 엔진을 부르는 사이에 상태가 먼저
    움직이면 엔진은 조용히 되돌아간다(설계대로다). 그 "조용히"가 1회인지
    계속인지를 가르는 것이 이 카운터다 — 여기서는 진짜 경합 경로 그대로,
    ADVANCE를 고른 뒤 상태가 이미 앞서 있는 상황을 만든다.
    """
    rid = "REQ-S-skip"
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회원가입",
            # 리컨실러는 PLANNED를 관측하고 ADVANCE(planner)를 골랐지만, 잠금을
            # 놓은 사이 다른 경로가 이미 IMPLEMENTING으로 옮겨 놨다.
            state=RequirementState.IMPLEMENTING.value, revision=1, run_id=f"run-{rid}",
        )
    )
    await session.commit()

    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))
    rec = Reconciler(session_maker=maker, engine=workflow)
    now = datetime.now(timezone.utc)
    try:
        await rec._execute(rid, Action(ADVANCE, ("planner",)), now)
        await rec._execute(rid, Action(ADVANCE, ("planner",)), now)

        events = [
            e
            for span in spans.get_finished_spans()
            for e in span.events
            if e.name == "vsi.transition_skipped"
        ]
        assert len(events) == 2, f"스킵 이벤트 2개를 기대했다: {events}"
        assert [e.attributes["vsi.transition_skip_count"] for e in events] == [1, 2], (
            "반복되는 스킵(정체)과 1회성 스킵(자기 치유)을 가르는 값이다"
        )
        first = dict(events[0].attributes)
        assert first["vsi.requirement_id"] == rid
        assert first["vsi.expected_state"] == "planned"
        assert first["vsi.actual_state"] == "implementing"
        assert first["vsi.signal"] == "plan_ready"
    finally:
        await db.dispose()


# ------------------------------------------------ Task 11 리뷰 라운드 1 회귀


async def test_give_up_with_no_tasks_still_escalates_the_requirement(session) -> None:
    """`run_s` backstop이 실제로 요구사항을 끝내는지 **DB 부수효과**로 고정한다.

    처음 구현은 `Action(GIVE_UP)`을 `tasks=()`인 채로 돌려줬고, `_execute`의
    GIVE_UP 분기는 `for task in action.tasks: ... engine.give_up(...)`뿐이었다
    — 빈 튜플이면 루프 본문이 한 번도 안 돌아 `engine.give_up`이 전혀
    불리지 않는다(리뷰가 실측: 호출 0회). 그러면 리컨실러는 매 주기 "→
    give_up"을 로그로 남기면서도 요구사항을 영원히 ACTIVE에 방치한다 — 이
    파일의 다른 테스트들처럼 `next_action`이 돌려준 kind만 보면 이 회귀를
    잡지 못한다(의도는 맞고 집행이 없었다). 네 ACTIVE 상태 전부에서
    `give_up_on_budget`이 실제로 ESCALATED까지 전이시키는지 확인한다 —
    `give_up`과 달리 REMEDIATING도 포함한다(시간 예산은 네 상태 모두에 걸린다).
    """
    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))
    rec = Reconciler(session_maker=maker, engine=workflow)
    try:
        for state in (
            RequirementState.PLANNED,
            RequirementState.IMPLEMENTING,
            RequirementState.VERIFYING,
            RequirementState.REMEDIATING,
        ):
            rid = f"REQ-BUDGET-{state.value}"
            session.add(
                WorkflowRequirement(
                    requirement_id=rid, title="회원가입",
                    state=state.value, revision=1, run_id=f"run-{rid}",
                )
            )
            await session.commit()

            await rec._execute(rid, Action(GIVE_UP), datetime.now(timezone.utc))

            async with maker() as s:
                req = await s.get(WorkflowRequirement, rid)
                assert req.state == RequirementState.ESCALATED.value, (
                    f"{state.value}에서 GIVE_UP(tasks=())이 요구사항을 끝내지 못했다"
                )
    finally:
        await db.dispose()


async def test_run_budget_end_to_end_escalates_remediating_with_zero_open_rows(
    session,
) -> None:
    """리뷰가 지목한 정확한 시나리오 — `remediate` 중간에 죽어 이번 회차 Task가
    0개인 REMEDIATING 요구사항이 `run_s`를 넘겼을 때, `next_action`부터
    `Reconciler.reconcile_once`까지 전체 경로가 실제로 요구사항을 ESCALATED로
    끝내는지 본다(단위 함수 하나가 아니라 배선 전체를 실제 DB로 검증한다).
    """
    rid = "REQ-BUDGET-E2E"
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회원가입",
            state=RequirementState.REMEDIATING.value, revision=2, run_id=f"run-{rid}",
            created_at=old, updated_at=old,
        )
    )
    await session.commit()

    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))
    # 요구사항 나이(2시간)가 확실히 run_s(1시간)를 넘기게 잡는다.
    rec = Reconciler(session_maker=maker, engine=workflow, stale_after_s=5.0, run_s=3600.0)
    try:
        reconciled = await rec.reconcile_once()
        assert reconciled == 1

        async with maker() as s:
            req = await s.get(WorkflowRequirement, rid)
            assert req.state == RequirementState.ESCALATED.value
    finally:
        await db.dispose()
