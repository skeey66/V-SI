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
from collections.abc import Callable
from contextlib import asynccontextmanager
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
BOOT_TIMEOUT_S = 120.0  # kill·재기동을 반복하면 도커가 느려진다(실측: 60초를 넘긴 적 있음)


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


async def _purge_abandoned(maker: async_sessionmaker, keep: str) -> None:
    """이전 실행이 남긴 **비종료** 요구사항을 전부 지운다.

    Task 13의 리컨실러가 오케스트레이터 안에서 상시로 도는 순간부터, 죽은 채 남은
    비종료 요구사항은 영원한 재디스패치 대상이 된다 — 스텁 에이전트의 호출 카운터와
    verdict 커서를 소모해 **다음 시나리오의 결정성을 깨뜨린다**. 실행 직전에
    청소하는 것이 테스트 층의 책임이다(운영 코드는 그 행들을 정당하게 복구한다).
    """
    async with maker() as s:
        ids = (
            await s.execute(
                select(WorkflowRequirement.requirement_id).where(
                    WorkflowRequirement.state.notin_([s_.value for s_ in TERMINAL]),
                    WorkflowRequirement.requirement_id != keep,
                )
            )
        ).scalars().all()
    for requirement_id in ids:
        await _purge(maker, requirement_id)


@asynccontextmanager
async def db_session():
    """테스트가 DB를 직접 읽고 쓰기 위한 세션. 엔진은 매번 새로 열고 닫는다."""
    engine = make_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:
            yield s
    finally:
        await engine.dispose()


async def purge(requirement_id: str) -> None:
    """요구사항 하나의 흔적을 지운다(손으로 만든 크래시 상태 정리용)."""
    engine = make_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _purge(maker, requirement_id)
    finally:
        await engine.dispose()


async def wait_for(
    requirement_id: str,
    predicate: Callable[[ScenarioResult], bool],
    timeout_s: float = TERMINAL_TIMEOUT_S,
    what: str = "조건",
) -> ScenarioResult:
    """DB를 폴링해 술어가 참이 될 때까지 기다린다.

    `asyncio.sleep(1.5)` 같은 시간 기반 대기 대신 **관측 기반** 대기다 — SIGKILL
    시점을 시계가 아니라 워크플로의 실제 진행 상태에 고정해야 테스트가 흔들리지 않는다.
    """
    engine = make_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    last: ScenarioResult | None = None
    try:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        while loop.time() < end:
            async with maker() as s:
                req = await s.get(WorkflowRequirement, requirement_id)
                if req is not None:
                    last = await collect(s, requirement_id)
                    if predicate(last):
                        return last
            await asyncio.sleep(POLL_INTERVAL_S)
    finally:
        await engine.dispose()
    raise AssertionError(
        f"{timeout_s}초 안에 {what}에 도달하지 못했다 "
        f"(마지막 관측: {last.state if last else None}, "
        f"revision={last.revision if last else None})"
    )


async def _compose_async(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", *args, cwd=REPO_ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args)} 실패 (rc={proc.returncode}): "
            f"{err.decode(errors='replace')}"
        )


async def kill_orchestrator() -> None:
    """오케스트레이터만 SIGKILL한다. 에이전트 4종은 계속 살아 있다.

    `depends_on`은 기동 순서 게이트일 뿐 런타임 연결이 아니므로 에이전트는
    영향을 받지 않는다 — 크래시 복구가 시험하려는 상황(오케스트레이터만 증발)이
    정확히 이것이다.
    """
    await _compose_async("kill", "-s", "SIGKILL", "orchestrator")


async def restart_orchestrator() -> None:
    """오케스트레이터**만** 다시 띄우고 헬스체크가 통과할 때까지 기다린다.

    `docker compose up -d`(서비스 이름 없이)를 쓰면 안 된다 — 에이전트 4종이
    오케스트레이터의 healthcheck 뒤로 직렬화되며 **재생성**되어, 시험 중인 시나리오
    카운터가 초기화된다.
    """
    await _compose_async("up", "-d", "orchestrator")
    loop = asyncio.get_running_loop()
    end = loop.time() + BOOT_TIMEOUT_S
    async with httpx.AsyncClient(timeout=3) as c:
        while loop.time() < end:
            try:
                if (await c.get(f"{ORCHESTRATOR_URL}/healthz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError("오케스트레이터 재기동 대기 시간 초과")


async def run_scenario(
    scenario_path: str,
    requirement_id: str,
    title: str,
    run_id: str | None = None,
) -> ScenarioResult:
    """compose로 기동된 에이전트를 상대로 워크플로를 끝까지 돌린다.

    청소가 **에이전트 재기동보다 먼저**인 것이 중요하다. 재기동은 수 초가 걸리는데,
    그 사이 DB에는 같은 `requirement_id`의 지난 실행 결과가 그대로 남아 있다.
    Task 13의 SIGKILL 테스트처럼 이 코루틴과 **동시에** DB를 관찰하는 쪽이 있으면
    지난 실행의 행(이미 accepted)을 이번 실행으로 착각한다(실측: kill 시점 술어가
    즉시 참이 되어 테스트가 공회전했다).
    """
    engine = make_engine(DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _purge(maker, requirement_id)
        await _purge_abandoned(maker, keep=requirement_id)
        await reset_agents(scenario_path)
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
