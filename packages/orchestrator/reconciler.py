"""리컨실리에이션 루프 — 크래시 복구.

**이벤트를 재생하지 않는다.** 현재 상태를 관찰해 목표 상태로 수렴시킨다
(쿠버네티스 컨트롤러 패턴). 그래서 복구가 특수 경로가 아니라 평시에도 도는
같은 루프이고, "크래시 복구 코드만 한 번도 안 돌아봤다"가 성립할 수 없다.

관찰 대상은 두 가지뿐이다: 요구사항 행의 상태·회차와, 그 회차에 속한 Task 행들.
그 둘만으로 다음에 할 일 하나를 고르는 것이 `next_action`이며, 순수 함수라
DB도 컨테이너도 없이 시험할 수 있다(`tests/orchestrator/test_reconciler_plan.py`).

복구하는 상태 네 가지(앞의 셋은 앞선 구현자들이 DB에서 실제로 관측한 것이다):

1. **푸시가 영영 오지 않는 `working` 행.** 에이전트의 executor가 크래시하면
   SDK는 푸시를 보내지 않는다(Task 11 실측: 정상 4회 → 크래시 2회). 디스패치
   직후의 `get_task` 안전망도 대개 크래시 착륙 전을 읽는다. 오래 머문 행을
   폴링해 권위 있게 다시 읽는 것 말고는 알아낼 방법이 없다.
2. **`remediating` + 올라간 revision + 그 revision의 Task 0개.** `remediate`가
   두 트랜잭션이라 사이에서 죽으면 남는 조합이다. 중단된 환류를 이어받는다.
3. **`submitted` + `a2a_task_id` NULL.** `submit()` 자체가 터진 경우. 에이전트
   쪽에 물어볼 id조차 없으므로 그 행은 실패로 확정하고 새 행으로 다시 보낸다.
4. **오케스트레이터 자체의 SIGKILL.** 죽어 있는 동안 도착한 푸시는 전부 유실된다.
   재기동 후 위 관찰만으로 중단 지점을 이어받는다.

지키는 규칙:

- **진행 중인 작업에 손대지 않는다.** 요구사항의 마지막 활동이 `stale_after_s`
  안쪽이면 건너뛴다. 살아 있는 디스패치와 경합하지 않기 위한 첫 번째 안전장치다.
- **Task 행은 불변이다.** 죽은 행을 되살리지 않는다. 실패로 확정하고 **새 행**을
  만든다.
- **한 번에 한 걸음.** 관찰 → 동작 하나 → 다음 주기에 다시 관찰. 여러 걸음을
  한 번에 몰아 하면 중간 상태를 관찰하지 않은 채 추측으로 진행하게 된다.
- **판단의 재료는 관측뿐이다.** "몇 번 실패했는가"·"얼마나 오래 열려 있는가"처럼
  행에서 직접 읽히는 사실만 쓴다. 상태 전이 규칙 자체는 여기 없다 — 그건
  `workflow.py`와 `engine._transition`에만 있다. (이 자리에는 한때 "횟수 제한도
  분류도 두지 않는다"고 적혀 있었다. 바로 아래 캡이 생기면서 거짓이 됐고,
  같은 docstring 안에서 다음 문단과 모순됐다.)

Task 12 추가: **재시도 상한(캡)**. `dispatch_agent`의 `attempt`(= 그 회차의 행 수)에는
스스로 상한이 없다 — 영원히 크래시하는 에이전트가 있으면 리컨실러가 영원히
재디스패치한다. 그래서 캡을 여기(`next_action`)에 둔다: 관측된 실패 행 수가
`retry.max_attempts(failure_class)`에 닿으면 DISPATCH 대신 `GIVE_UP`을 고른다.
캡의 근거는 리컨실리에이션 로직 자체(관찰→동작 매핑)가 아니라 "몇 번이나
시도했는가"라는 순수 관측이므로, 여기 두어도 리컨실러 소관을 벗어나지 않는다
(엔진의 상태 전이 규칙 자체는 여전히 `workflow.py`와 `engine._transition`에만 있다).

리뷰 라운드 1 추가: **PROBE가 영원히 끝나지 않는 경우의 안전망.** 실측으로
확인한 것 — a2a-sdk 1.1.2는 같은 컨테이너에서 연속으로 두 번째 크래시가 나면
이벤트 소비자가 종료 상태로의 전이를 누락하는 경우가 있다(Producer 쪽 로그는
남지만 Consumer 쪽 "Failed" 처리가 없다). 그러면 `get_task`가 영원히
`TASK_STATE_WORKING`을 돌려주고, `refresh_task`는 비종료 상태에서 그냥
돌아가므로 실패 행이 생기지 않는다 — 캡은 실패 행 수를 세는데 셀 게 없으니
캡도 작동하지 않고 PROBE만 매 주기 반복된다. "모든 요구사항은 언젠가 종료
상태에 도달한다"는 이 프로젝트의 핵심 보장이 외부 SDK의 정확성에 인질로
잡히면 안 된다. 그래서 열린 행 자체의 나이에 **별도 상한**(`stuck_after_s`,
`stale_after_s`보다 훨씬 크다)을 두어, 그걸 넘기면 더 묻지 않고
`on_task_failed(..., EXECUTION)`으로 강제 확정한다(`FORCE_FAIL`). 이후로는
평범한 실패 행이라 캡이 정상적으로 이어받는다. SDK 버그 자체는 고치지
않는다 — 우리 층의 보장이 그 버그의 존재 여부와 무관해지도록 만들 뿐이다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from opentelemetry import trace
from sqlalchemy import func, select

from orchestrator.engine import (
    OPEN_TASK_STATES,
    TASK_COMPLETED,
    TASK_FAILED,
    VERIFIERS,
    WorkflowEngine,
)
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.retry import FailureClass, max_attempts
from orchestrator.workflow import RequirementState

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

S = RequirementState

#: 아직 끝나지 않은 요구사항 상태. 종료 상태(accepted/escalated)와 승인 대기
#: (blocked)는 리컨실러가 건드리지 않는다 — blocked를 푸는 것은 사람이다.
ACTIVE: tuple[RequirementState, ...] = (
    S.PLANNED, S.IMPLEMENTING, S.VERIFYING, S.REMEDIATING,
)

PROBE = "probe"            # 에이전트에 권위 있게 물어 열린 행을 확정한다
DISPATCH = "dispatch"      # 빠진 에이전트를 새 Task로 보낸다
ADVANCE = "advance"        # Task는 끝났는데 상태가 따라가지 못했다
FINISH = "finish"          # 두 verdict가 다 모였는데 판정이 유실됐다
REMEDIATE = "remediate"    # 중단된 환류를 이어받는다
GIVE_UP = "give_up"        # 재시도 예산을 다 썼다 — 더 디스패치하지 않고 포기한다
FORCE_FAIL = "force_fail"  # PROBE로도 안 끝난다 — 더 묻지 않고 실패로 확정한다

DEFAULT_INTERVAL_S = 2.0
DEFAULT_STALE_AFTER_S = 5.0
#: 이보다 오래 열린 채면 에이전트 응답을 더 기다리지 않는다(SDK 결함 안전망).
#: `stale_after_s`보다 한 자릿수 이상 커야 한다 — 정상적으로 느린 실행까지
#: 강제로 끊으면 안 되고, SDK가 종료 상태를 영영 안 줄 때만 걸려야 한다.
DEFAULT_STUCK_AFTER_S = 60.0


@dataclass(frozen=True)
class Action:
    """요구사항 하나에 대해 지금 할 일 하나."""

    kind: str
    agents: tuple[str, ...] = ()
    tasks: tuple[WorkflowTask, ...] = field(default=())

    def __str__(self) -> str:  # 로그용
        if self.tasks:
            return f"{self.kind}({','.join(t.agent for t in self.tasks)})"
        return f"{self.kind}({','.join(self.agents)})" if self.agents else self.kind


def last_activity(req: WorkflowRequirement, rows: list[WorkflowTask]) -> datetime:
    """이 요구사항에서 마지막으로 무언가 움직인 시각.

    요구사항 행의 `updated_at`만으로는 부족하다 — 디스패치는 Task 행을 넣을 뿐
    요구사항 행을 건드리지 않기 때문이다. 그래서 Task 행의 시각까지 함께 본다.
    """
    newest = req.updated_at or req.created_at
    for t in rows:
        for ts in (t.created_at, t.completed_at):
            if ts is not None and ts > newest:
                newest = ts
    return newest


def _exhausted_row(current: list[WorkflowTask], agent: str) -> WorkflowTask | None:
    """이 회차에서 `agent`의 재시도 예산이 바닥났으면 그 근거 행을 돌려준다.

    행이 불변이라는 규칙과 맞물린다 — 캡을 넘겼다고 기존 실패 행을 지우거나
    고치지 않는다. 그저 **새 행을 더 만들지 않을 뿐**이다. `failure_class`가
    비어 있는 실패(옛 행, 또는 `submit()` 이전에 죽어 분류가 안 된 경우)는
    EXECUTION으로 본다 — POISON(1회)만큼 성급하지 않고 TRANSPORT(3회)만큼
    낙관적이지도 않은 중간값이다.

    반환값은 `GIVE_UP` 액션의 `tasks`에 실린다 — 리뷰 라운드 1: 리컨실러가
    "어떤 에이전트가 어떤 분류로 예산을 다 썼는지"를 실행부(`_execute`)에
    함께 넘겨야 그 정보가 아웃박스 이벤트까지 닿는다.
    """
    failed = [t for t in current if t.agent == agent and t.state == TASK_FAILED]
    if not failed:
        return None
    worst = max(failed, key=lambda t: t.attempt)
    fc = FailureClass(worst.failure_class) if worst.failure_class else FailureClass.EXECUTION
    return worst if len(failed) >= max_attempts(fc) else None


def next_action(
    req: WorkflowRequirement,
    rows: list[WorkflowTask],
    now: datetime,
    stale_after_s: float,
    stuck_after_s: float,
) -> Action | None:
    """관측에서 다음 동작 하나를 고른다. 순수 함수 — 부수 효과가 없다.

    `now`는 **DB 서버 시계**여야 한다(행의 시각도 전부 DB가 찍는다). 호스트와
    컨테이너의 시계 차이가 staleness 판정에 섞이면 조용히 오작동한다.
    """
    state = RequirementState(req.state)
    if state not in ACTIVE:
        return None
    if (now - last_activity(req, rows)).total_seconds() < stale_after_s:
        return None  # 진행 중일 수 있다 — 손대지 않는다.

    current = [t for t in rows if t.revision == req.revision]
    open_rows = tuple(t for t in current if t.state in OPEN_TASK_STATES)
    if open_rows:
        # 리뷰 라운드 1: PROBE로 물어봐도 SDK가 영원히 비종료 상태만 돌려줄 수
        # 있다(실측한 a2a-sdk 1.1.2 결함) — 그러면 실패 행이 안 생겨 캡도
        # 작동하지 않는다. 열린 행 자체가 `stuck_after_s`보다 오래됐으면 더
        # 묻지 않고 강제로 실패 확정한다. 한 번에 한 걸음: 갇힌 행만 처리하고
        # 나머지 열린 행은 다음 주기에 정상적으로 PROBE한다.
        stuck = tuple(
            t for t in open_rows
            if (now - t.created_at).total_seconds() >= stuck_after_s
        )
        if stuck:
            return Action(FORCE_FAIL, tasks=stuck)
        # 열려 있는데 오래 조용하다. 추측하지 않고 에이전트에 직접 묻는다.
        return Action(PROBE, tasks=open_rows)

    done = {t.agent for t in current if t.state == TASK_COMPLETED}

    if state is S.PLANNED:
        if "planner" in done:
            return Action(ADVANCE, ("planner",))
        exhausted = _exhausted_row(current, "planner")
        if exhausted is not None:
            return Action(GIVE_UP, ("planner",), tasks=(exhausted,))
        return Action(DISPATCH, ("planner",))
    if state is S.IMPLEMENTING:
        if "dev" in done:
            return Action(ADVANCE, ("dev",))
        exhausted = _exhausted_row(current, "dev")
        if exhausted is not None:
            return Action(GIVE_UP, ("dev",), tasks=(exhausted,))
        return Action(DISPATCH, ("dev",))
    if state is S.VERIFYING:
        missing = tuple(v for v in VERIFIERS if v not in done)
        if not missing:
            return Action(FINISH)
        exhausted_rows = tuple(
            r for r in (_exhausted_row(current, v) for v in missing) if r is not None
        )
        if exhausted_rows:
            return Action(GIVE_UP, tuple(t.agent for t in exhausted_rows), tasks=exhausted_rows)
        return Action(DISPATCH, missing)
    return Action(REMEDIATE)  # S.REMEDIATING


class Reconciler:
    def __init__(
        self,
        session_maker,
        engine: WorkflowEngine,
        interval_s: float = DEFAULT_INTERVAL_S,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        stuck_after_s: float = DEFAULT_STUCK_AFTER_S,
    ) -> None:
        self._sm = session_maker
        self._engine = engine
        self._interval = interval_s
        self._stale_after = stale_after_s
        self._stuck_after = stuck_after_s
        # 운영 신호 1(Task 14): 같은 task_id가 PROBE된 반복 횟수. a2a-sdk 1.1.2가
        # 종료 전이를 누락하면(리컨실러 docstring 참고) 이 값이 매 주기 계속
        # 올라간다 — "PROBE가 비정상적으로 오래 반복된다"는 그 자체로는 로그를
        # 뒤져야 알 수 있던 증상을 span 속성으로 바로 드러낸다.
        self._probe_counts: dict[str, int] = {}

    async def reconcile_once(self) -> int:
        """한 바퀴 돌며 수렴 동작을 수행한다. 조정한 요구사항 수를 돌려준다."""
        async with self._sm() as s:
            now = (await s.execute(select(func.now()))).scalar_one()
            ids = (
                await s.execute(
                    select(WorkflowRequirement.requirement_id).where(
                        WorkflowRequirement.state.in_([x.value for x in ACTIVE])
                    )
                )
            ).scalars().all()

        reconciled = 0
        for requirement_id in ids:
            try:
                if await self._reconcile_one(requirement_id, now):
                    reconciled += 1
            except Exception:
                # 요구사항 하나의 실패가 나머지를 막지 않는다. 다음 주기에 다시 본다.
                logger.exception("리컨실 실패: %s", requirement_id)
        return reconciled

    async def run_forever(self) -> None:
        while True:
            try:
                n = await self.reconcile_once()
                if n:
                    logger.info("리컨실: 요구사항 %d건 조정", n)
            except asyncio.CancelledError:
                raise
            except Exception:  # 루프는 절대 죽지 않는다
                logger.exception("리컨실 루프 예외 — 계속한다")
            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------ 내부

    async def _reconcile_one(self, requirement_id: str, now: datetime) -> bool:
        async with self._sm() as s:
            # `remediate`·`maybe_finish`와 같은 규율: 요구사항 행을 잠그고 읽는다.
            # 살아 있는 디스패치가 커밋 중이면 그 커밋 뒤의 상태를 본다.
            req = (
                await s.execute(
                    select(WorkflowRequirement)
                    .where(WorkflowRequirement.requirement_id == requirement_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if req is None:
                return False
            rows = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.requirement_id == requirement_id
                    )
                )
            ).scalars().all()
            action = next_action(
                req, list(rows), now, self._stale_after, self._stuck_after
            )

        if action is None:
            return False
        logger.info(
            "리컨실 %s: state=%s revision=%d → %s",
            requirement_id, req.state, req.revision, action,
        )
        await self._execute(requirement_id, action, now)
        return True

    async def _execute(self, requirement_id: str, action: Action, now: datetime) -> None:
        """잠금을 놓은 뒤에 실행한다 — 엔진이 자기 트랜잭션을 열기 때문이다.

        `now`는 `next_action`이 이 동작을 고를 때 쓴 것과 같은 DB 서버 시계
        스냅샷이다 — PROBE 반복·FORCE_FAIL 시점의 "행 나이"를 재는 기준을
        판정 로직과 일치시킨다(운영 신호 1·2, Task 14).
        """
        with tracer.start_as_current_span(
            f"reconciler.{action.kind}",
            attributes={"vsi.requirement_id": requirement_id},
        ):
            if action.kind == PROBE:
                for task in action.tasks:
                    if task.a2a_task_id is None:
                        # 우리가 받은 id가 없다 = 에이전트 쪽 대응물을 특정할 수 없다.
                        # submit() 왕복 자체가 안 됐다는 뜻이라 TRANSPORT로 분류한다.
                        await self._engine.on_task_failed(
                            task.task_id, "no_agent_task", FailureClass.TRANSPORT
                        )
                        continue
                    # 운영 신호 1: 이 task_id가 PROBE된 반복 횟수와, PROBE 시점의
                    # 열린 행 나이(대략적인 "실행 지속 시간"). a2a-sdk 1.1.2의
                    # 종료 전이 누락 버그는 이 span 하나만 봐도 드러난다 —
                    # 반복 횟수가 계속 올라가는데 나이도 같이 올라가면 그 태스크는
                    # 절대 안 끝난다는 뜻이다.
                    self._probe_counts[task.task_id] = self._probe_counts.get(task.task_id, 0) + 1
                    with tracer.start_as_current_span(
                        "reconciler.probe_task",
                        attributes={
                            "vsi.task_id": task.task_id,
                            "vsi.agent": task.agent,
                            "vsi.probe.repetition": self._probe_counts[task.task_id],
                            "vsi.task.open_age_s": (now - task.created_at).total_seconds(),
                        },
                    ):
                        await self._engine.refresh_task(task.agent, task.a2a_task_id)
            elif action.kind == DISPATCH:
                for agent in action.agents:
                    await self._engine.dispatch_agent(requirement_id, agent)
            elif action.kind == ADVANCE:
                await self._engine.advance(requirement_id, action.agents[0])
            elif action.kind == FINISH:
                await self._engine.maybe_finish(requirement_id)
            elif action.kind == REMEDIATE:
                await self._engine.remediate(requirement_id)
            elif action.kind == GIVE_UP:
                # 여러 에이전트가 동시에 예산을 다 썼어도(드물다 — qa·security가
                # 같은 주기에 함께 소진) 전이는 한 번만 성공한다. 첫 번째 뒤엔
                # 상태가 이미 ESCALATED라 이후 호출은 engine.give_up의 expected
                # 가드에 걸려 조용히 반환된다 — 두 번째 이후 에이전트의 사유는
                # 이벤트에 남지 않지만, 상태 전이가 중복되거나 터지지는 않는다.
                for task in action.tasks:
                    fc = FailureClass(task.failure_class) if task.failure_class else FailureClass.EXECUTION
                    await self._engine.give_up(requirement_id, task.agent, fc)
            elif action.kind == FORCE_FAIL:
                # 리뷰 라운드 1: PROBE로도 끝나지 않는 행(SDK 결함으로 종료 상태가
                # 영영 안 오는 경우)에 대한 안전망. 더 묻지 않고 실행 중 원인
                # 불명 오류로 확정한다 — 이후로는 평범한 실패 행이라 다음 주기의
                # 캡 계산이 정상적으로 이어받는다.
                for task in action.tasks:
                    # 운영 신호 2: FORCE_FAIL이 발동하는 순간의 열린 행 나이.
                    # `stuck_after_s`(기본 60초)는 지금까지 추측값이었다 — 이
                    # 분포가 쌓여야 그 값을 근거를 갖고 조정할 수 있다.
                    with tracer.start_as_current_span(
                        "reconciler.force_fail",
                        attributes={
                            "vsi.task_id": task.task_id,
                            "vsi.agent": task.agent,
                            "vsi.task.open_age_s": (now - task.created_at).total_seconds(),
                            "vsi.stuck_after_s": self._stuck_after,
                        },
                    ):
                        await self._engine.on_task_failed(
                            task.task_id, "stuck_beyond_ceiling", FailureClass.EXECUTION
                        )
            else:  # pragma: no cover - 방어적
                raise ValueError(f"알 수 없는 동작: {action.kind}")
