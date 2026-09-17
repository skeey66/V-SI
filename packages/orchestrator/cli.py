"""단일 명령 데모 진입점.

`scripts/demo.sh`가 `python -m orchestrator.cli start ...`로 이 모듈을 부른다.
전제는 `docker compose up -d --build`로 스택 전체(오케스트레이터·에이전트
4종·게이트웨이·Jaeger·postgres)가 이미 떠 있다는 것이다 — 이 CLI는 새 스택을
띄우지 않는다.

**요구사항 착수는 이미 떠 있는 오케스트레이터 컨테이너의 HTTP API
(`POST /requirements`)를 두드려서 한다** — CLI 프로세스 안에서 두 번째
`WorkflowEngine`을 새로 만들지 않는다. 그 핸들러 몸통이 그대로
`await workflow.start(...)`이므로(`orchestrator/main.py` 참고) 실질적으로는
`WorkflowEngine.start()`를 호출하는 것과 같지만, 이 경로를 타는 이유가 하나
더 있다: 오케스트레이터 컨테이너는 도커 네트워크 안에서 에이전트를
`http://<서비스명>:8000`으로, 자기 자신을 향한 푸시 콜백을
`http://orchestrator:8000/push`로 부른다. CLI는 호스트에서 돈다 — 도커 내부
호스트명을 못 찾는다. CLI 전용으로 호스트에 노출된 포트(8001~8004)를 향하는
두 번째 엔진을 만들면, 에이전트로 나가는 호출과 에이전트가 보내는 푸시
콜백이 서로 다른 네트워크 경로를 타게 되어(호스트 게시 포트 vs 도커 내부
호스트명) 실제로 매 시연·CI에서 검증되는 컨테이너 내부 경로와 미묘하게
다른 코드 경로를 시험하게 된다. 이미 떠 있는 컨테이너의 API를 그대로
때리면 그럴 위험이 없다.

시나리오 주입(`--scenario`)은 `tests/integration/harness.reset_agents`와
같은 방식이다 — 에이전트 프로세스 안의 verdict 커서·호출 카운터는 상태
기계라 시나리오를 바꾸려면 프로세스를 새로 띄우는 수밖에 없다. 대상은
에이전트 4종뿐이다(`--no-deps`) — 오케스트레이터를 건드리면 그 헬스체크
뒤로 에이전트 재생성이 직렬화된다(레슨 참고).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import make_engine
from orchestrator.models import Artifact, OutboxEvent, WorkflowRequirement, WorkflowTask
from orchestrator.workflow import TERMINAL, RequirementState

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ORCHESTRATOR_URL = os.environ.get("VSI_ORCHESTRATOR_URL", "http://localhost:8000")
#: 호스트에서 도는 이 CLI가 DB를 직접 만질 때 쓰는 접속 문자열. 컴포즈가
#: postgres를 55432로 게시한다(docker-compose.yml).
DEFAULT_DB_URL = os.environ.get(
    "VSI_CLI_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)
AGENT_SERVICES = ("planner", "dev", "qa", "security")
#: 호스트에 게시된 에이전트 포트(docker-compose.yml). 도커 내부 호스트명이 아니다.
AGENT_HEALTH_PORTS = {"planner": 8001, "dev": 8002, "qa": 8003, "security": 8004}

POLL_INTERVAL_S = 0.5
AGENT_BOOT_TIMEOUT_S = 120.0
_TERMINAL_VALUES = {s.value for s in TERMINAL}


def _compose(*args: str, scenario: str | None = None) -> None:
    env = dict(os.environ)
    if scenario is not None:
        env["VSI_SCENARIO"] = scenario
    proc = subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args)} 실패 (rc={proc.returncode})\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


async def _wait_agents_healthy(timeout_s: float = AGENT_BOOT_TIMEOUT_S) -> None:
    targets = [f"http://localhost:{p}/healthz" for p in AGENT_HEALTH_PORTS.values()]
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
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
        raise RuntimeError(f"에이전트 기동 대기 시간 초과: {pending}")


async def _purge(requirement_id: str) -> None:
    """같은 requirement_id로 데모를 반복 실행할 수 있도록 이전 흔적을 지운다."""
    engine = make_engine(DEFAULT_DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
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
            await s.execute(delete(Artifact).where(Artifact.requirement_id == requirement_id))
            await s.execute(delete(WorkflowTask).where(WorkflowTask.requirement_id == requirement_id))
            await s.execute(
                delete(WorkflowRequirement).where(
                    WorkflowRequirement.requirement_id == requirement_id
                )
            )
            await s.commit()
    finally:
        await engine.dispose()


async def _poll_until_terminal(requirement_id: str, base_url: str, timeout_s: float) -> str:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    state: str | None = None
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as c:
        while loop.time() < end:
            r = await c.get(f"/requirements/{requirement_id}")
            r.raise_for_status()
            state = r.json()["state"]
            print(f"[{requirement_id}] {state}", flush=True)
            if state in _TERMINAL_VALUES:
                return state
            await asyncio.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"{timeout_s}초 안에 종료 상태에 도달하지 못했다 (마지막 관측: {state})")


async def _start(
    requirement_id: str,
    title: str,
    run_id: str,
    scenario: str | None,
    base_url: str,
    timeout_s: float,
) -> str:
    await _purge(requirement_id)
    if scenario is not None:
        print(f"시나리오 주입: {scenario} (에이전트 4종 강제 재생성)", flush=True)
        _compose(
            "up", "-d", "--force-recreate", "--no-deps", *AGENT_SERVICES, scenario=scenario
        )
        await _wait_agents_healthy()
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
        resp = await c.post(
            "/requirements",
            json={"requirement_id": requirement_id, "title": title, "run_id": run_id},
        )
        resp.raise_for_status()
    return await _poll_until_terminal(requirement_id, base_url, timeout_s)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="요구사항 하나를 제출하고 종료 상태까지 대기한다")
    start_p.add_argument("--requirement", required=True, dest="requirement_id")
    start_p.add_argument("--title", required=True)
    start_p.add_argument(
        "--scenario", default=None, help="에이전트 4종에 주입할 시나리오 YAML 경로"
    )
    start_p.add_argument("--run-id", default=None)
    start_p.add_argument("--base-url", default=DEFAULT_ORCHESTRATOR_URL)
    start_p.add_argument("--timeout", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command != "start":  # pragma: no cover - argparse가 이미 걸러낸다
        return 2

    run_id = args.run_id or f"run-{args.requirement_id}"
    try:
        state = asyncio.run(
            _start(
                args.requirement_id,
                args.title,
                run_id,
                args.scenario,
                args.base_url,
                args.timeout,
            )
        )
    except Exception as exc:  # noqa: BLE001 — CLI 경계: 사람이 읽을 에러로 바꿔 낸다
        print(f"실패: {exc}", file=sys.stderr)
        return 1

    print(f"최종 상태: {state}")
    return 0 if state == RequirementState.ACCEPTED.value else 2


if __name__ == "__main__":
    raise SystemExit(main())
