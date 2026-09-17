"""`verdict`를 만드는 것은 **누가 끝났는가**이지 페이로드에 무엇이 들어 있는가가 아니다.

엔진의 불변식 4번은 "verdict는 에이전트의 주장이 아니라 exit_code에서 도출한다"다.
종료 코드에서 도출하는 부분은 처음부터 맞았지만, **그 도출을 할지 말지**가
`"verdict" in payload`로 정해져 있었다 — 즉 에이전트가 키 하나를 빼는 것만으로
심판을 면제받을 수 있었다. 누가 검증자인지는 오케스트레이터가 이미 알고 있다
(`VERIFIERS`). 그러니 판정 여부도 우리가 정한다.

그 구멍이 만드는 결과가 조용하다는 것이 최악이다: `completed` + `verdict IS NULL`인
qa/security 행은 `maybe_finish`의 verdict 개수 검사에 영원히 못 미쳐 판정이 나지
않는데, 리컨실러의 두 천장은 어느 쪽도 이 행을 보지 못한다 — 갇힘 천장은 **열린**
행만 보고, 재시도 캡은 **실패** 행만 센다. 요구사항은 `verifying`에서 FINISH를
매 주기 반복하며 영원히 돈다.
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.engine import WorkflowEngine
from orchestrator.models import WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


async def _verifying(session, rid: str):
    """VERIFYING 상태의 요구사항 하나와 열린 qa·security 행을 만든다."""
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회원가입",
            state=RequirementState.VERIFYING.value, revision=1, run_id=f"run-{rid}",
        )
    )
    for agent in ("qa", "security"):
        session.add(
            WorkflowTask(
                requirement_id=rid, agent=agent, revision=1,
                a2a_task_id=f"a2a-{rid}-{agent}",
                idempotency_key=f"k-{rid}-{agent}", state="working", attempt=1,
            )
        )
    await session.commit()
    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    # 클라이언트가 없어도 된다 — 검증자 완료는 디스패치로 이어지지 않는다.
    return db, maker, WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))


async def test_verifier_without_verdict_key_still_gets_judged(session) -> None:
    """`verdict` 키가 없는 검증자 완료도 판정을 받아 요구사항이 끝까지 간다."""
    rid = "REQ-V-novkey"
    db, maker, workflow = await _verifying(session, rid)
    try:
        # 스텁이 verdict 키를 빼고 보냈다. 종료 코드는 그대로 0이다.
        await workflow.on_task_completed(
            f"a2a-{rid}-qa", {"kind": "test_report", "agent": "qa", "exit_code": 0}
        )
        await workflow.on_task_completed(
            f"a2a-{rid}-security",
            {"kind": "security_report", "agent": "security", "exit_code": 0},
        )
        async with maker() as s:
            req = await s.get(WorkflowRequirement, rid)
            assert RequirementState(req.state) is RequirementState.ACCEPTED, (
                "verdict 키가 없다는 이유로 verifying에 갇히면 안 된다"
            )
            rows = (
                await s.execute(
                    select(WorkflowTask).where(WorkflowTask.requirement_id == rid)
                )
            ).scalars().all()
            assert {r.agent: r.verdict for r in rows} == {
                "qa": "PASS", "security": "PASS",
            }
    finally:
        await db.dispose()


async def test_verdict_still_comes_from_exit_code_not_the_claim(session) -> None:
    """도출 규칙 자체는 그대로다 — 0이 아니면 FAIL이고, 주장은 무시한다."""
    rid = "REQ-V-exitcode"
    db, maker, workflow = await _verifying(session, rid)
    try:
        # 에이전트는 PASS를 주장하지만 종료 코드는 1이다.
        await workflow.on_task_completed(
            f"a2a-{rid}-qa",
            {"kind": "test_report", "agent": "qa", "exit_code": 1, "verdict": "PASS"},
        )
        async with maker() as s:
            row = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.requirement_id == rid,
                        WorkflowTask.agent == "qa",
                    )
                )
            ).scalar_one()
            assert row.verdict == "FAIL"
            # 한쪽 verdict만으로는 판정하지 않는다.
            req = await s.get(WorkflowRequirement, rid)
            assert RequirementState(req.state) is RequirementState.VERIFYING
    finally:
        await db.dispose()


async def test_non_verifier_never_gets_a_verdict(session) -> None:
    """검증자가 아닌 에이전트는 무엇을 보내든 verdict를 받지 않는다.

    `maybe_finish`가 verdict 개수를 세므로, dev 행에 verdict가 생기면 회차
    판정의 분모가 조용히 오염된다.
    """
    rid = "REQ-V-nonverifier"
    session.add(
        WorkflowRequirement(
            requirement_id=rid, title="회원가입",
            state=RequirementState.IMPLEMENTING.value, revision=1, run_id=f"run-{rid}",
        )
    )
    session.add(
        WorkflowTask(
            requirement_id=rid, agent="dev", revision=1, a2a_task_id=f"a2a-{rid}-dev",
            idempotency_key=f"k-{rid}-dev", state="working", attempt=1,
        )
    )
    await session.commit()
    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    workflow = WorkflowEngine(maker, {}, TimeoutConfig.from_env({}))
    try:
        # dev가 verdict를 자칭해도 무시된다. (advance가 검증자를 디스패치하려
        # 하지만 클라이언트가 없어 KeyError — 완료 기록은 그 전에 커밋된다.)
        try:
            await workflow.on_task_completed(
                f"a2a-{rid}-dev",
                {"kind": "source_code", "agent": "dev", "exit_code": 1,
                 "verdict": "FAIL"},
            )
        except KeyError:
            pass
        async with maker() as s:
            row = (
                await s.execute(
                    select(WorkflowTask).where(
                        WorkflowTask.requirement_id == rid,
                        WorkflowTask.agent == "dev",
                    )
                )
            ).scalar_one()
            assert row.state == "completed"
            assert row.verdict is None
    finally:
        await db.dispose()
