"""워크플로 엔진.

작업 순서·판정 조건은 전부 여기 있는 결정적 코드다 — LLM은 관여하지 않는다.
엔진이 지키는 불변식 네 가지:

1. 에이전트끼리 직접 부르지 않는다. 모든 디스패치는 이 클래스를 통한다.
2. 상태 전이와 아웃박스 이벤트는 **한 트랜잭션**에 들어간다.
3. `workflow_tasks` 행은 불변이다 — 완료된 행을 되돌리지 않는다. 재시도·환류는
   새 행을 만든다(Task 11·12).
4. `verdict`는 에이전트의 주장이 아니라 `exit_code`에서 도출한다.

이 태스크(Task 10)는 해피 패스만 완주한다. 환류 루프·반복 상한은 Task 11,
재시도는 Task 12, 리컨실리에이션은 Task 13 소관이라 여기엔 없다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.a2a_client import AgentClient
from orchestrator.idempotency import idempotency_key
from orchestrator.models import Artifact, WorkflowRequirement, WorkflowTask
from orchestrator.outbox import record_event
from orchestrator.policy import TimeoutConfig
from orchestrator.workflow import RequirementState, WorkflowSignal, next_state

logger = logging.getLogger(__name__)

#: 순차 파이프라인. 각 단계는 앞 단계가 끝나야 시작한다.
PIPELINE = ["planner", "dev"]
#: 검증 에이전트. 동시에 돌고, 둘의 verdict가 모두 도착해야 판정한다.
VERIFIERS = ["qa", "security"]

TASK_SUBMITTED = "submitted"
TASK_WORKING = "working"
TASK_COMPLETED = "completed"


def _content_hash(payload: dict) -> str:
    """아티팩트 내용의 결정적 해시.

    브리프 스케치는 `str(hash(str(payload)))`를 썼지만 파이썬의 내장 `hash`는
    프로세스마다 시드가 달라(PYTHONHASHSEED) 같은 내용이 다른 값을 낳는다.
    Task 11이 이 해시를 멱등성 키의 입력으로 쓰므로 결정적이어야 한다.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkflowEngine:
    def __init__(
        self,
        session_maker,
        clients: dict[str, AgentClient],
        timeouts: TimeoutConfig,
    ) -> None:
        self._sm = session_maker
        self._clients = clients
        self._timeouts = timeouts

    # ---------------------------------------------------------------- 진입점

    async def start(self, requirement_id: str, title: str, run_id: str) -> None:
        async with self._sm() as s:
            s.add(
                WorkflowRequirement(
                    requirement_id=requirement_id,
                    title=title,
                    state=RequirementState.PLANNED.value,
                    revision=1,
                    run_id=run_id,
                )
            )
            record_event(
                s,
                "requirement",
                requirement_id,
                "state_changed",
                {"to": RequirementState.PLANNED.value, "signal": None},
            )
            await s.commit()
        await self.dispatch_agent(requirement_id, "planner")

    async def dispatch_agent(self, requirement_id: str, agent: str) -> None:
        """에이전트 1기에 작업을 제출한다.

        공개 메서드다 — Task 13의 리컨실리에이터가 바깥에서 호출한다.
        """
        async with self._sm() as s:
            req = await s.get(WorkflowRequirement, requirement_id)
            if req is None:
                raise LookupError(f"알 수 없는 requirement_id: {requirement_id}")
            key = idempotency_key(requirement_id, agent, req.revision, [])
            task = WorkflowTask(
                requirement_id=requirement_id,
                agent=agent,
                revision=req.revision,
                idempotency_key=key,
                state=TASK_SUBMITTED,
            )
            s.add(task)
            await s.flush()
            record_event(
                s,
                "task",
                task.task_id,
                "task_submitted",
                {
                    "agent": agent,
                    "requirement_id": requirement_id,
                    "revision": req.revision,
                },
            )
            await s.commit()
            task_id = task.task_id

        a2a_id = await self._clients[agent].submit(
            {"requirement_id": requirement_id}, key
        )

        async with self._sm() as s:
            t = await s.get(WorkflowTask, task_id)
            t.a2a_task_id, t.state = a2a_id, TASK_WORKING
            await s.commit()

        # 경합 구간 닫기: 에이전트가 a2a_task_id를 우리가 적기도 전에 끝내고 푸시를
        # 보냈을 수 있다. 그런 푸시는 상관시킬 행이 없어 버려지므로, 행을 적은 직후
        # 한 번 권위 있는 읽기를 해서 이미 끝났으면 여기서 처리한다.
        # (푸시 시각 P와 행 커밋 시각 W에 대해: P > W면 푸시가 처리하고, P < W면
        #  이 조회 시각 G > W > P이므로 조회가 종료 상태를 본다. 양쪽이 겹쳐도
        #  on_task_completed가 행 잠금으로 직렬화하고 중복을 버린다.)
        snapshot = await self._clients[agent].get_task(a2a_id)
        if snapshot.is_terminal:
            await self.on_task_completed(a2a_id, snapshot.payload)

    # ------------------------------------------------------------ 완료 처리

    async def on_push_notification(self, body: dict) -> None:
        """SDK `BasePushNotificationSender`가 보낸 콜백 본문을 해석한다.

        본문은 `MessageToDict(to_stream_response(event))`라 camelCase JSON이며
        `task` / `statusUpdate` / `artifactUpdate` 중 하나가 들어 있다(브리프
        스케치의 `{"task_id": ..., "payload": ...}`가 아니다 — 실제 SDK 형식에
        맞춘다). 종료 상태 전이만 의미가 있고 나머지는 흘려보낸다.
        """
        update = body.get("statusUpdate")
        if not isinstance(update, dict):
            return
        a2a_task_id = update.get("taskId")
        state = (update.get("status") or {}).get("state")
        if not a2a_task_id or not state:
            return

        async with self._sm() as s:
            t = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.a2a_task_id == a2a_task_id
                    )
                )
            ).scalar_one_or_none()
            agent = t.agent if t is not None else None
        if agent is None:
            # 아직 a2a_task_id가 기록되지 않았다. dispatch_agent의 디스패치 직후
            # 확인이 이 완료를 잡는다.
            logger.info("상관시킬 수 없는 푸시를 버린다: %s", a2a_task_id)
            return

        snapshot = await self._clients[agent].get_task(a2a_task_id)
        if not snapshot.is_terminal:
            return
        await self.on_task_completed(a2a_task_id, snapshot.payload)

    async def on_task_completed(self, a2a_task_id: str, payload: dict) -> None:
        """완료된 Task를 기록하고 다음 단계를 결정한다.

        푸시와 디스패치 직후 확인이 동시에 같은 완료를 물고 올 수 있으므로 행을
        `FOR UPDATE`로 잠그고, 이미 완료된 행이면 조용히 되돌아간다. 이 중복
        차단이 없으면 아티팩트 유니크 제약 위반과 이중 진행이 난다.
        """
        async with self._sm() as s:
            t = (
                await s.execute(
                    select(WorkflowTask)
                    .where(WorkflowTask.a2a_task_id == a2a_task_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if t is None:
                logger.warning("알 수 없는 a2a_task_id: %s", a2a_task_id)
                return
            if t.state == TASK_COMPLETED:
                return  # 중복 알림. Task 행은 불변이므로 되돌리지 않는다.

            t.state = TASK_COMPLETED
            t.completed_at = datetime.now(timezone.utc)
            if "verdict" in payload:
                # 에이전트의 주장이 아니라 종료 코드가 판정을 만든다.
                t.verdict = "PASS" if payload.get("exit_code") == 0 else "FAIL"

            kind = payload.get("kind")
            if kind:
                s.add(
                    Artifact(
                        requirement_id=t.requirement_id,
                        producer_task=t.task_id,
                        kind=kind,
                        version=t.revision,
                        content=payload,
                        sha256=_content_hash(payload),
                    )
                )
            record_event(
                s,
                "task",
                t.task_id,
                "task_completed",
                {"agent": t.agent, "verdict": t.verdict, "kind": kind},
            )
            await s.commit()
            requirement_id, agent = t.requirement_id, t.agent

        await self._advance(requirement_id, agent)

    # -------------------------------------------------------------- 진행 결정

    async def _advance(self, requirement_id: str, finished_agent: str) -> None:
        if finished_agent == "planner":
            await self._transition(requirement_id, WorkflowSignal.PLAN_READY)
            await self.dispatch_agent(requirement_id, "dev")
        elif finished_agent == "dev":
            await self._transition(requirement_id, WorkflowSignal.DEV_DONE)
            # 디스패치는 순차지만 실행은 병렬이다: submit은 Task 생성 즉시
            # 반환하므로(return_immediately) qa와 security는 각자의 프로세스에서
            # 동시에 돈다.
            for verifier in VERIFIERS:
                await self.dispatch_agent(requirement_id, verifier)
        else:
            await self._maybe_finish(requirement_id)

    async def _transition(self, requirement_id: str, signal: WorkflowSignal) -> None:
        async with self._sm() as s:
            req = (
                await s.execute(
                    select(WorkflowRequirement)
                    .where(WorkflowRequirement.requirement_id == requirement_id)
                    .with_for_update()
                )
            ).scalar_one()
            req.state = next_state(RequirementState(req.state), signal).value
            record_event(
                s,
                "requirement",
                requirement_id,
                "state_changed",
                {"to": req.state, "signal": signal.value},
            )
            await s.commit()

    async def _maybe_finish(self, requirement_id: str) -> None:
        """qa/security 두 verdict가 모두 도착했을 때만 판정한다.

        두 콜백이 동시에 들어오면 둘 다 "verdict 2개"를 보고 각자 전이를 시도해
        두 번째가 `IllegalTransition`으로 터진다. 요구사항 행을 `FOR UPDATE`로
        잠가 판정을 직렬화하고, 이미 VERIFYING을 벗어났으면 되돌아간다.
        """
        async with self._sm() as s:
            req = (
                await s.execute(
                    select(WorkflowRequirement)
                    .where(WorkflowRequirement.requirement_id == requirement_id)
                    .with_for_update()
                )
            ).scalar_one()
            if RequirementState(req.state) is not RequirementState.VERIFYING:
                return  # 이미 판정됐다.

            rows = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.requirement_id == requirement_id,
                        WorkflowTask.revision == req.revision,
                        WorkflowTask.agent.in_(VERIFIERS),
                    )
                )
            ).scalars().all()
            verdicts = [r.verdict for r in rows if r.verdict is not None]
            if len(verdicts) < len(VERIFIERS):
                return

            signal = (
                WorkflowSignal.VERDICTS_PASS
                if all(v == "PASS" for v in verdicts)
                else WorkflowSignal.VERDICTS_FAIL
            )
            req.state = next_state(RequirementState(req.state), signal).value
            record_event(
                s,
                "requirement",
                requirement_id,
                "state_changed",
                {"to": req.state, "signal": signal.value},
            )
            await s.commit()

    # ------------------------------------------------------------------ 조회

    async def state_of(self, requirement_id: str) -> RequirementState:
        async with self._sm() as s:
            req = await s.get(WorkflowRequirement, requirement_id)
            if req is None:
                raise LookupError(f"알 수 없는 requirement_id: {requirement_id}")
            return RequirementState(req.state)
