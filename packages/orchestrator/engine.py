"""워크플로 엔진.

작업 순서·판정 조건은 전부 여기 있는 결정적 코드다 — LLM은 관여하지 않는다.
엔진이 지키는 불변식 네 가지:

1. 에이전트끼리 직접 부르지 않는다. 모든 디스패치는 이 클래스를 통한다.
2. 상태 전이와 아웃박스 이벤트는 **한 트랜잭션**에 들어간다.
3. `workflow_tasks` 행은 불변이다 — 완료된 행을 되돌리지 않는다. 재시도·환류는
   새 행을 만든다(Task 11·12).
4. `verdict`는 에이전트의 주장이 아니라 `exit_code`에서 도출한다.

Task 10이 해피 패스를, Task 11이 환류 루프와 반복 상한을 채웠다. Task 12는
`submit()` 왕복이 그 자리에서 터지는 경우(전송/실행/독성입력)에 재시도·백오프를
붙였다. 제출은 됐는데 그 뒤 executor가 크래시해 푸시가 안 오는 경우는 여전히
리컨실러(Task 13)의 몫이다 — 두 실패는 "아직 Task가 생기지 않았다" vs "Task는
생겼는데 죽었다"로 층이 다르다.

**재개 가능한 공개 진입점**(Task 13): `dispatch_agent` / `advance` /
`maybe_finish` / `remediate` / `refresh_task` / `on_task_failed`는 리컨실러가
바깥에서 부른다. 크래시는 이 엔진을 호출 사슬 중간에서 끊어 놓으므로, 각 단계가
**끊긴 지점부터 다시 불릴 수 있어야** 한다. 리컨실러가 상태 기계를 흉내 내지
않게 하려면(전이 규칙이 두 곳에 생기면 반드시 갈라진다) 그 단계들이 사유화된
채로 남아 있어선 안 된다. 대신 각 메서드는 자기 전제를 스스로 검사한다 —
`maybe_finish`는 VERIFYING인지와 verdict 개수를, `remediate`는 REMEDIATING인지와
이번 회차가 소비됐는지를 확인한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from orchestrator.a2a_client import TERMINAL_TASK_STATES, AgentClient
from orchestrator.idempotency import idempotency_key
from orchestrator.models import Artifact, WorkflowRequirement, WorkflowTask
from orchestrator.outbox import record_event
from orchestrator.policy import TimeoutConfig
from orchestrator.retry import FailureClass, backoff_seconds, classify, max_attempts
from orchestrator.workflow import RequirementState, WorkflowSignal, next_state

logger = logging.getLogger(__name__)

#: 순차 파이프라인. 각 단계는 앞 단계가 끝나야 시작한다.
PIPELINE = ["planner", "dev"]
#: 검증 에이전트. 동시에 돌고, 둘의 verdict가 모두 도착해야 판정한다.
VERIFIERS = ["qa", "security"]

TASK_SUBMITTED = "submitted"
TASK_WORKING = "working"
TASK_COMPLETED = "completed"
#: 에이전트 Task가 산출물 없이 죽었다. `verdict == "FAIL"`(정상 완료, 산출물 있음)과는
#: 다른 것이다 — 이 행은 되살릴 수 없고 새 행으로 대체된다(Task 13).
TASK_FAILED = "failed"

#: 아직 결과가 확정되지 않은 Task 행. 리컨실러(Task 13)가 이 상태의 행만 캔다.
OPEN_TASK_STATES = (TASK_SUBMITTED, TASK_WORKING)
TERMINAL_ROW_STATES = (TASK_COMPLETED, TASK_FAILED)


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

        `attempt`는 같은 (요구사항, 에이전트, 회차)에 이미 있는 행 수 + 1이다.
        크래시로 죽은 행을 대체하는 디스패치가 몇 번째인지를 사실로 기록할 뿐,
        **이 번호 자체에는 상한이 없다** — 반복 재디스패치를 몇 번까지 허용할지는
        리컨실러의 `next_action`이 결정한다(Task 12, 캡은 관측된 실패 행 수와
        `failure_class`로 계산한다). 멱등성 키에도 이 번호가 들어간다(유니크
        제약이 있는데 Task 행은 불변이라 새 행을 만들어야 하므로, 회차만으로는
        키가 충돌한다).

        `submit()` 자체가 그 자리에서 터지면(연결 거부·5xx·스키마 오류 등)
        아직 에이전트 쪽에 Task가 생기지 않았으므로 **같은 행, 같은 멱등성
        키로 그 자리에서 재시도한다**(Task 12) — 새 행을 만드는 것은 이미
        디스패치된 뒤 죽은 경우(리컨실러 소관)에만 해당한다. 재시도 예산을
        다 쓰면 이 행을 실패로 확정하고 분류를 남긴다. `refresh_task`는 그
        경우 부르지 않는다 — 물어볼 `a2a_task_id`가 없다.
        """
        async with self._sm() as s:
            req = await s.get(WorkflowRequirement, requirement_id)
            if req is None:
                raise LookupError(f"알 수 없는 requirement_id: {requirement_id}")
            attempt = (
                await s.execute(
                    select(func.count())
                    .select_from(WorkflowTask)
                    .where(
                        WorkflowTask.requirement_id == requirement_id,
                        WorkflowTask.agent == agent,
                        WorkflowTask.revision == req.revision,
                    )
                )
            ).scalar_one() + 1
            key = idempotency_key(requirement_id, agent, req.revision, [], attempt)
            # 계보 연결: Task 행은 불변이므로 환류는 직전 회차 행을 고쳐 쓰지 않고
            # 새 행을 만들어 `revision_of`로 가리킨다. 첫 회차(revision 1)에는
            # 직전 회차가 없어 자연히 NULL이 된다.
            prev = (
                await s.execute(
                    select(WorkflowTask)
                    .where(
                        WorkflowTask.requirement_id == requirement_id,
                        WorkflowTask.agent == agent,
                        WorkflowTask.revision == req.revision - 1,
                    )
                    .order_by(WorkflowTask.created_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            task = WorkflowTask(
                requirement_id=requirement_id,
                agent=agent,
                revision=req.revision,
                idempotency_key=key,
                state=TASK_SUBMITTED,
                attempt=attempt,
                revision_of=prev.task_id if prev is not None else None,
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
                    "attempt": attempt,
                },
            )
            await s.commit()
            task_id = task.task_id

        # 딕셔너리 조회는 재시도 루프 **밖**에 둔다: 알 수 없는 에이전트는 설정
        # 오류이지 제출 실패가 아니다 — `KeyError`로 그 자리에서 터져야 한다
        # (분류·재시도 대상은 `client.submit()`이 던지는 예외뿐이다).
        client = self._clients[agent]

        a2a_id: str | None = None
        last_failure: FailureClass | None = None
        submit_attempt = 1
        while True:
            try:
                a2a_id = await asyncio.wait_for(
                    client.submit({"requirement_id": requirement_id}, key),
                    timeout=self._timeouts.tool_s,
                )
                break
            except Exception as exc:
                last_failure = classify(exc)
                if submit_attempt >= max_attempts(last_failure):
                    logger.warning(
                        "%s 제출을 %d회 만에 포기한다 (%s): %s",
                        agent, submit_attempt, last_failure.value, exc,
                    )
                    break
                await asyncio.sleep(backoff_seconds(submit_attempt))
                submit_attempt += 1

        async with self._sm() as s:
            t = await s.get(WorkflowTask, task_id)
            if a2a_id is None:
                t.state = TASK_FAILED
                t.failure_class = last_failure.value
                t.completed_at = datetime.now(timezone.utc)
                record_event(
                    s,
                    "task",
                    task_id,
                    "task_failed",
                    {
                        "agent": agent,
                        "requirement_id": requirement_id,
                        "revision": t.revision,
                        "attempt": t.attempt,
                        "failure_class": last_failure.value,
                        "submit_attempts": submit_attempt,
                        "observed": "submit_raised",
                    },
                )
            else:
                t.a2a_task_id, t.state = a2a_id, TASK_WORKING
            await s.commit()

        if a2a_id is None:
            # 재시도 예산을 다 썼다. 이 행은 종결됐다 — 다시 보낼지는 리컨실러의
            # `next_action`이 관측된 실패 행 수로 결정한다(Task 12의 캡).
            return

        # 경합 구간 닫기: 에이전트가 a2a_task_id를 우리가 적기도 전에 끝내고 푸시를
        # 보냈을 수 있다. 그런 푸시는 상관시킬 행이 없어 버려지므로, 행을 적은 직후
        # 한 번 권위 있는 읽기를 해서 이미 끝났으면 여기서 처리한다.
        # (푸시 시각 P와 행 커밋 시각 W에 대해: P > W면 푸시가 처리하고, P < W면
        #  이 조회 시각 G > W > P이므로 조회가 종료 상태를 본다. 양쪽이 겹쳐도
        #  on_task_completed가 행 잠금으로 직렬화하고 중복을 버린다.)
        await self.refresh_task(agent, a2a_id)

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
        if state not in TERMINAL_TASK_STATES:
            # 한 Task 당 푸시가 네 번 온다(Task 생성 / working / artifact / completed).
            # 종료 전이가 아니면 여기서 끊는다 — 그러지 않으면 중간 상태마다 에이전트에
            # 불필요한 get_task 왕복이 한 번씩 더 붙는다.
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

        await self.refresh_task(agent, a2a_task_id)

    async def refresh_task(self, agent: str, a2a_task_id: str) -> str:
        """에이전트에 권위 있게 물어 우리 행을 현재 사실에 맞춘다.

        완료 감지 경로가 하나로 모인다: 디스패치 직후 안전망도, 푸시 콜백도,
        리컨실러(Task 13)도 전부 이 메서드를 통한다 — "어떻게 알게 됐는가"에
        따라 판정이 갈라지면 안 되기 때문이다. 결과 상태를 문자열로 돌려준다.

        **종료했는데 산출물이 하나도 없으면 executor 크래시로 본다.** verdict
        FAIL도 A2A 상태로는 `TASK_STATE_FAILED`라(스텁이 `updater.failed()`를
        부른다) 상태 이름만으로는 둘을 구분할 수 없다. 구분하는 것은 산출물의
        유무다: 정상 실행은 exit_code가 0이든 아니든 아티팩트를 남기고, 크래시한
        실행은 아무것도 남기지 못한다.
        """
        snapshot = await self._clients[agent].get_task(a2a_task_id)
        if not snapshot.is_terminal:
            return snapshot.state
        if snapshot.payload:
            await self.on_task_completed(a2a_task_id, snapshot.payload)
            return TASK_COMPLETED

        async with self._sm() as s:
            t = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.a2a_task_id == a2a_task_id
                    )
                )
            ).scalar_one_or_none()
            task_id = t.task_id if t is not None else None
        if task_id is None:
            logger.warning("알 수 없는 a2a_task_id: %s", a2a_task_id)
            return TASK_FAILED
        # 산출물 없이 종료했다 = executor가 크래시했다는 뜻이다(위 docstring).
        # 원인 불명의 실행 중 오류이므로 EXECUTION으로 분류한다.
        await self.on_task_failed(task_id, snapshot.state, FailureClass.EXECUTION)
        return TASK_FAILED

    async def on_task_failed(
        self,
        task_id: str,
        observed: str,
        failure_class: FailureClass | None = None,
    ) -> None:
        """Task 행을 실패로 확정한다. **관측된 사실**과 그 분류를 함께 남긴다.

        여기서 재시도하지 않는다 — 누락된 작업을 다시 디스패치할지, 몇 번까지
        허용할지는 수렴 루프(리컨실러)의 `next_action`이 결정한다(Task 12).
        이 메서드가 하는 일은 "이 행은 더 이상 기다릴 대상이 아니다"와 "왜
        끝났는가"를 영속화하는 것뿐이다 — `failure_class`가 리컨실러의 캡
        계산 입력이 된다.

        Task 행은 불변이라는 규칙은 지켜진다: 완료된 행은 건드리지 않고, 열린
        행만 종료 상태로 확정한다. 대체 작업은 **새 행**으로 만들어진다.
        """
        async with self._sm() as s:
            t = (
                await s.execute(
                    select(WorkflowTask)
                    .where(WorkflowTask.task_id == task_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if t is None or t.state in TERMINAL_ROW_STATES:
                return  # 이미 확정됐다(중복 관측).
            t.state = TASK_FAILED
            t.failure_class = failure_class.value if failure_class else None
            t.completed_at = datetime.now(timezone.utc)
            record_event(
                s,
                "task",
                t.task_id,
                "task_failed",
                {
                    "agent": t.agent,
                    "requirement_id": t.requirement_id,
                    "revision": t.revision,
                    "attempt": t.attempt,
                    "observed": observed,
                    "failure_class": t.failure_class,
                },
            )
            await s.commit()

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
            if t.state in TERMINAL_ROW_STATES:
                return  # 중복 알림(또는 이미 실패 확정). Task 행은 불변이라 되돌리지 않는다.

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

        await self.advance(requirement_id, agent)

    # -------------------------------------------------------------- 진행 결정

    async def advance(self, requirement_id: str, finished_agent: str) -> None:
        """끝난 에이전트에 따라 다음 단계를 연다.

        **전제를 스스로 검사한다**(형제 메서드 `maybe_finish`·`remediate`와 같은
        규율). 리컨실러는 요구사항 행의 잠금을 놓은 뒤에 이 메서드를 부르므로, 그
        사이에 푸시가 도착해 상태가 먼저 움직일 수 있다. 그때는 아무 것도 하지 않고
        돌아간다 — 이미 다른 경로가 같은 일을 했다는 뜻이다. 예외로 알리지 않는
        이유는 이것이 **오류가 아니라 정상적인 경합 결과**이기 때문이다(다음 리컨실
        주기가 현재 상태를 다시 관찰한다).

        전제 검사는 `_transition`의 잠금 **안에서** 일어난다. 여기서 따로 읽고
        나서 전이하면 그 둘 사이가 다시 창이 되어, 막으려던 경합이 그대로 남는다.
        """
        if finished_agent == "planner":
            if not await self._transition(
                requirement_id, WorkflowSignal.PLAN_READY,
                expected=RequirementState.PLANNED,
            ):
                return
            await self.dispatch_agent(requirement_id, "dev")
        elif finished_agent == "dev":
            if not await self._transition(
                requirement_id, WorkflowSignal.DEV_DONE,
                expected=RequirementState.IMPLEMENTING,
            ):
                return
            # 디스패치는 순차지만 실행은 병렬이다: submit은 Task 생성 즉시
            # 반환하므로(return_immediately) qa와 security는 각자의 프로세스에서
            # 동시에 돈다.
            for verifier in VERIFIERS:
                await self.dispatch_agent(requirement_id, verifier)
        else:
            # 검증 에이전트는 자기 차례에 전이를 만들지 않는다. 판정은 두 verdict가
            # 모두 모였을 때만 일어나고, 그 전제 검사는 maybe_finish가 갖고 있다.
            await self.maybe_finish(requirement_id)

    async def _transition(
        self,
        requirement_id: str,
        signal: WorkflowSignal,
        expected: RequirementState | None = None,
    ) -> bool:
        """상태를 전이하고 같은 트랜잭션에 이벤트를 남긴다. 전이했으면 True.

        `expected`를 주면 **잠금 안에서** 현재 상태를 확인하고, 다르면 아무 것도
        하지 않고 False를 돌려준다. 호출자가 미리 읽어 두고 비교하는 방식으로는
        읽기와 전이 사이가 창으로 남아 같은 경합이 재현된다 — 그래서 검사를 여기
        잠금 안으로 들여왔다. `expected`가 없으면 종전대로 허용되지 않는 신호에
        `IllegalTransition`을 던진다(호출자가 전제를 이미 보장하는 경로들이다).
        """
        async with self._sm() as s:
            req = (
                await s.execute(
                    select(WorkflowRequirement)
                    .where(WorkflowRequirement.requirement_id == requirement_id)
                    .with_for_update()
                )
            ).scalar_one()
            if expected is not None and RequirementState(req.state) is not expected:
                logger.info(
                    "전이를 건너뛴다: %s는 %s를 기대했으나 이미 %s다",
                    requirement_id, expected.value, req.state,
                )
                return False
            req.state = next_state(RequirementState(req.state), signal).value
            record_event(
                s,
                "requirement",
                requirement_id,
                "state_changed",
                {"to": req.state, "signal": signal.value},
            )
            await s.commit()
            return True

    async def maybe_finish(self, requirement_id: str) -> None:
        """qa/security 두 verdict가 모두 도착했을 때만 판정한다.

        두 콜백이 동시에 들어오면 둘 다 "verdict 2개"를 보고 각자 전이를 시도해
        두 번째가 `IllegalTransition`으로 터진다. 요구사항 행을 `FOR UPDATE`로
        잠가 판정을 직렬화하고, 이미 VERIFYING을 벗어났으면 되돌아간다.

        **두 verdict가 모두 모였을 때만 판정한다는 전제를 Task 11도 지킨다.**
        FAIL 하나만 보고 먼저 환류를 시작하면, 아직 디스패치되지 않았거나 실행
        중인 다른 검증 에이전트가 있는 채로 revision이 올라가 `_advance`의 검증
        루프가 회차를 넘나들며 Task를 중복 생성한다. 그래서 환류 분기는 verdict
        수 검사 **뒤에만** 있다.
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
            failed = signal is WorkflowSignal.VERDICTS_FAIL

        # 환류는 판정 트랜잭션을 닫은 뒤에 시작한다. 이 시점에 상태는 이미
        # REMEDIATING이라, 뒤늦게 락을 얻은 다른 verdict 콜백은 위의 VERIFYING
        # 가드에 걸려 되돌아간다 — 환류가 두 번 시작되지 않는다.
        if failed:
            await self.remediate(requirement_id)

    async def remediate(self, requirement_id: str) -> None:
        """반복 상한을 확인하고, 남아 있으면 revision을 올려 새 dev Task를 만든다.

        상한은 워크플로 층(요구사항 행의 `max_revisions`)에 있다 — 에이전트가
        영원히 FAIL을 내도 여기서 멈추므로 에이전트가 우회할 수 없다.

        되돌리는 대신 앞으로 간다: 기존 Task 행은 그대로 두고 revision을 올려
        dev부터 새 행을 만든다(`dispatch_agent`가 `revision_of`로 계보를 잇는다).

        **다시 불러도 안전하다**(Task 13). 이 메서드는 두 트랜잭션이라(회차 증가 /
        상태 전이) 사이에서 프로세스가 죽으면 `remediating` + 올라간 revision +
        그 revision의 Task 0개가 남는다. 리컨실러가 그 상태를 보고 이 메서드를
        다시 부르므로, 회차를 **두 번** 올리지 않도록 "이번 회차가 이미 소비됐는가"
        (= 이번 revision에 Task 행이 있는가)를 증가 조건으로 둔다. 상한 검사도
        증가할 때만 한다 — 이미 올라간 회차는 그때 검사를 통과한 것이다.
        """
        async with self._sm() as s:
            req = (
                await s.execute(
                    select(WorkflowRequirement)
                    .where(WorkflowRequirement.requirement_id == requirement_id)
                    .with_for_update()
                )
            ).scalar_one()
            if RequirementState(req.state) is not RequirementState.REMEDIATING:
                return  # 다른 경로가 이미 이 환류를 진행시켰다.
            consumed = (
                await s.execute(
                    select(func.count())
                    .select_from(WorkflowTask)
                    .where(
                        WorkflowTask.requirement_id == requirement_id,
                        WorkflowTask.revision == req.revision,
                    )
                )
            ).scalar_one() > 0
            exceeded = consumed and req.revision >= req.max_revisions
            if consumed and not exceeded:
                req.revision += 1
                record_event(
                    s,
                    "requirement",
                    requirement_id,
                    "revision_started",
                    {"revision": req.revision},
                )
            await s.commit()

        if exceeded:
            await self._transition(requirement_id, WorkflowSignal.LIMIT_EXCEEDED)
            return
        await self._transition(requirement_id, WorkflowSignal.DEV_DONE)
        await self.dispatch_agent(requirement_id, "dev")

    async def give_up(self, requirement_id: str) -> None:
        """재시도 예산을 다 쓴 에이전트가 있으면 요구사항을 포기 상태로 보낸다.

        공개 메서드다 — 리컨실러의 `next_action`이 관측(실패 행 수와
        `failure_class`)에서 이미 "이 에이전트는 더 재시도해도 소용없다"를
        판단했다(Task 12의 캡). 여기서는 그 판단을 실행해 상태를 ESCALATED로
        확정할 뿐, 판단 자체를 다시 하지 않는다 — 판단 로직이 두 곳에 있으면
        갈라진다는 형제 메서드들의 규율을 그대로 따른다.

        `remediate`의 LIMIT_EXCEEDED(반복 상한 초과)와 같은 목적지지만 출발
        상태가 다르다: `remediate`는 REMEDIATING에서만 부르므로 이미 그
        전제를 확인했지만, `give_up`은 PLANNED/IMPLEMENTING/VERIFYING 중
        어디서나 불릴 수 있어 **먼저 관측한 상태를 `expected`로 넘겨** 그
        사이에 다른 경로가 먼저 전이시켰으면(`_transition`이 False) 조용히
        돌아간다.
        """
        async with self._sm() as s:
            req = await s.get(WorkflowRequirement, requirement_id)
        if req is None:
            return
        observed = RequirementState(req.state)
        if observed not in (
            RequirementState.PLANNED,
            RequirementState.IMPLEMENTING,
            RequirementState.VERIFYING,
        ):
            return  # 이미 다른 경로가 끝냈거나(터미널) REMEDIATING(다른 메서드 소관).
        transitioned = await self._transition(
            requirement_id, WorkflowSignal.LIMIT_EXCEEDED, expected=observed
        )
        if not transitioned:
            logger.info(
                "포기 전이를 건너뛴다: %s는 %s를 기대했으나 이미 움직였다",
                requirement_id, observed.value,
            )

    # ------------------------------------------------------------------ 조회

    async def state_of(self, requirement_id: str) -> RequirementState:
        async with self._sm() as s:
            req = await s.get(WorkflowRequirement, requirement_id)
            if req is None:
                raise LookupError(f"알 수 없는 requirement_id: {requirement_id}")
            return RequirementState(req.state)
