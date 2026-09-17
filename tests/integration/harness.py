"""통합 테스트 하네스.

compose로 기동된 스택(오케스트레이터 + 스텁 에이전트 4종 + postgres)을 상대로
요구사항 하나를 끝까지 돌리고, 결과를 DB에서 직접 읽어 돌려준다.

설계 원칙 두 가지:

1. **판정은 DB에서 읽는다.** 오케스트레이터가 응답으로 무엇을 말하든 믿지 않고,
   영속화된 상태·Task·아웃박스 이벤트만 본다. "상태는 바뀌었는데 이벤트는 유실"
   같은 결함은 API 응답으로는 드러나지 않는다.
2. **시나리오는 컨테이너 재기동으로 주입한다.** `AgentScenario`는 에이전트
   프로세스 안에 사는 상태 기계(verdict 커서·호출 카운터)라, 시나리오를 바꾸려면
   프로세스를 새로 띄우는 수밖에 없다. 같은 시나리오를 다시 돌릴 때도 강제
   재생성해 카운터를 0으로 되돌린다 — Task 11/12의 환류·재시도 시나리오는 이
   카운터에 결과가 좌우되므로, 빠름보다 결정성을 택한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.models import (
    Artifact,
    OutboxEvent,
    WorkflowRequirement,
    WorkflowTask,
)
from orchestrator.workflow import TERMINAL, RequirementState

REPO_ROOT = Path(__file__).resolve().parents[2]

DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)
ORCHESTRATOR_URL = os.environ.get("VSI_ORCHESTRATOR_URL", "http://localhost:8000")
AGENT_PORTS = {"planner": 8001, "dev": 8002, "qa": 8003, "security": 8004}

POLL_INTERVAL_S = 0.2
TERMINAL_TIMEOUT_S = float(os.environ.get("VSI_HARNESS_TIMEOUT_S", "90"))
BOOT_TIMEOUT_S = 60.0


@dataclass
class ScenarioResult:
    state: RequirementState  # str이 아니라 enum. 테스트가 `is`로 비교한다.
    revision: int
    task_count: int
    events: list
    tasks: list  # Task 11·12가 revision_of / attempt / 멱등성 키를 본다
    artifacts: list


async def collect(session, requirement_id: str) -> ScenarioResult:
    req = await session.get(WorkflowRequirement, requirement_id)
    assert req is not None, f"요구사항 행이 없다: {requirement_id}"
    tasks = (
        await session.execute(
            select(WorkflowTask)
            .where(WorkflowTask.requirement_id == requirement_id)
            .order_by(WorkflowTask.created_at, WorkflowTask.task_id)
        )
    ).scalars().all()
    events = (
        await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.aggregate_id == requirement_id)
            .order_by(OutboxEvent.event_id)
        )
    ).scalars().all()
    task_ids = [t.task_id for t in tasks]
    # 이벤트는 요구사항 집합체와 Task 집합체 두 갈래로 기록된다.
    task_events = (
        await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.aggregate_id.in_(task_ids))
            .order_by(OutboxEvent.event_id)
        )
    ).scalars().all() if task_ids else []
    artifacts = (
        await session.execute(
            select(Artifact).where(Artifact.requirement_id == requirement_id)
        )
    ).scalars().all()
    return ScenarioResult(
        state=RequirementState(req.state),
        revision=req.revision,
        task_count=len(tasks),
        events=sorted([*events, *task_events], key=lambda e: e.event_id),
        tasks=list(tasks),
        artifacts=list(artifacts),
    )


def _compose(*args: str, scenario: str | None = None) -> None:
    env = dict(os.environ)
    if scenario is not None:
        env["VSI_SCENARIO"] = scenario
    proc = subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args)} 실패 (rc={proc.returncode})\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


async def _wait_healthy(deadline_s: float = BOOT_TIMEOUT_S) -> None:
    targets = [f"http://localhost:{p}/healthz" for p in AGENT_PORTS.values()]
    targets.append(f"{ORCHESTRATOR_URL}/healthz")
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    async with httpx.AsyncClient(timeout=3) as c:
        pending = list(targets)
        while pending and loop.time() < end:
            still: list[str] = []
            for url in pending:
                try:
                    r = await c.get(url)
                    if r.status_code != 200:
                        still.append(url)
                except httpx.HTTPError:
                    still.append(url)
            pending = still
            if pending:
                await asyncio.sleep(0.3)
    if pending:
        raise RuntimeError(f"기동 대기 시간 초과: {pending}")


async def reset_agents(scenario_path: str) -> None:
    """에이전트 4종을 주어진 시나리오로 강제 재생성한다."""
    _compose(
        "up", "-d", "--force-recreate", "--no-deps",
        *AGENT_PORTS.keys(),
        scenario=scenario_path,
    )
    await _wait_healthy()


async def _purge(maker: async_sessionmaker, requirement_id: str) -> None:
    """같은 requirement_id로 재실행할 수 있도록 이전 흔적을 지운다."""
    async with maker() as s:
        tasks = (
            await s.execute(
                select(WorkflowTask.task_id).where(
                    WorkflowTask.requirement_id == requirement_id
                )
            )
        ).scalars().all()
        ids = [requirement_id, *tasks]
        await s.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(ids)))
        await s.execute(
            delete(Artifact).where(Artifact.requirement_id == requirement_id)
        )
        await s.execute(
            delete(WorkflowTask).where(WorkflowTask.requirement_id == requirement_id)
        )
        await s.execute(
            delete(WorkflowRequirement).where(
                WorkflowRequirement.requirement_id == requirement_id
            )
        )
        await s.commit()


async def run_scenario(
    scenario_path: str,
    requirement_id: str,
    title: str,
    run_id: str | None = None,
) -> ScenarioResult:
    """compose로 기동된 에이전트를 상대로 워크플로를 끝까지 돌린다."""
    await reset_agents(scenario_path)
    engine = make_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _purge(maker, requirement_id)
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(
                f"{ORCHESTRATOR_URL}/requirements",
                json={
                    "requirement_id": requirement_id,
                    "title": title,
                    "run_id": run_id or f"run-{requirement_id}",
                },
            )
            resp.raise_for_status()

        loop = asyncio.get_running_loop()
        end = loop.time() + TERMINAL_TIMEOUT_S
        state: RequirementState | None = None
        while loop.time() < end:
            async with maker() as s:
                req = await s.get(WorkflowRequirement, requirement_id)
                state = RequirementState(req.state) if req else None
            if state in TERMINAL:
                break
            await asyncio.sleep(POLL_INTERVAL_S)
        else:
            raise AssertionError(
                f"{TERMINAL_TIMEOUT_S}초 안에 종료 상태에 도달하지 못했다 "
                f"(마지막 상태: {state})"
            )

        async with maker() as s:
            return await collect(s, requirement_id)
    finally:
        await engine.dispose()
