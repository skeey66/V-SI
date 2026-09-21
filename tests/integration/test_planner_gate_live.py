"""기획 게이트를 **실제 스택 전 구간**에서 돌린다.

단위 시험(`tests/orchestrator/test_planner_gate.py`)은 엔진 메서드를 직접
불러 게이트 논리를 고정한다. 그 시험이 절대 못 보는 것이 세 가지 있다:

1. 에이전트가 A2A 로 보낸 `exit_code` 가 푸시 수신부를 지나 `WorkflowTask.verdict`
   까지 **실제로** 도달하는가
2. `blocked` 에 빠진 요구사항을 리컨실러가 정말 건드리지 않는가
   (`reconciler.ACTIVE` 에서 빠져 있다는 것은 선언일 뿐이다 — 상시로 도는
   리컨실러가 그 선언을 지키는지는 스택을 돌려봐야 안다)
3. `POST /requirements/{id}/approve` 가 실제로 개발을 디스패치하는가

전제: `docker compose up -d --build` 로 스택이 떠 있어야 한다. 하네스가
에이전트만 시나리오에 맞춰 재기동한다.
"""

from __future__ import annotations

import httpx
import pytest

from orchestrator.workflow import RequirementState
from tests.integration.harness import (
    ORCHESTRATOR_URL,
    purge,
    reset_agents,
    wait_for,
)

SCENARIO = "scenarios/planner_gate_blocks.yaml"


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


async def _start(requirement_id: str, title: str) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(
            f"{ORCHESTRATOR_URL}/requirements",
            json={
                "requirement_id": requirement_id,
                "title": title,
                "run_id": f"run-{requirement_id}",
            },
        )
        resp.raise_for_status()


async def _approve(requirement_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{ORCHESTRATOR_URL}/requirements/{requirement_id}/approve")
        resp.raise_for_status()
        return resp.json()


async def test_gate_blocks_then_approval_resumes_from_dev() -> None:
    """FAIL → `blocked` → 승인 → 개발부터 이어짐 → `accepted`.

    승인 뒤에 **기획이 다시 돌지 않는 것**이 요점이다. 게이트는 처음부터 다시
    하자는 것이 아니라 멈춰 세웠다가 그 자리에서 이어가자는 것이다 — 기획
    Task 가 하나뿐임을 단언해 그것을 고정한다.
    """
    rid = "REQ-GATE-LIVE-1"
    await purge(rid)
    await reset_agents(SCENARIO)
    await _start(rid, "회의실 예약")

    blocked = await wait_for(
        rid,
        lambda r: r.state is RequirementState.BLOCKED,
        what="승인 대기(blocked)",
    )
    # 목표가 비었는데 개발을 보내면 안 된다 — 게이트의 존재 이유다.
    assert [t.agent for t in blocked.tasks] == ["planner"]
    assert blocked.tasks[0].verdict == "FAIL"
    assert blocked.revision == 1

    body = await _approve(rid)
    assert body["granted"] == "true"
    assert body["state"] == RequirementState.IMPLEMENTING.value

    done = await wait_for(
        rid,
        lambda r: r.state is RequirementState.ACCEPTED,
        what="accepted",
    )
    by_agent = [t.agent for t in done.tasks]
    assert sorted(by_agent) == ["dev", "planner", "qa", "security"]
    assert by_agent.count("planner") == 1  # 기획은 다시 돌지 않았다.
    assert done.revision == 1


async def test_blocked_requirement_is_left_alone_by_the_reconciler() -> None:
    """`blocked` 는 리컨실러가 건드리지 않는다 — 푸는 것은 사람뿐이다.

    리컨실러는 오케스트레이터 컨테이너 안에서 상시로 돈다. 그 주기보다 넉넉히
    오래 `blocked` 에 머무르는지 본다: 자동으로 풀린다면 개발 Task 가 생기거나
    상태가 움직인다.

    이 시험이 없으면 `reconciler.ACTIVE` 에서 `BLOCKED` 를 빼먹는 회귀가
    조용히 지나간다 — 그 회귀의 증상은 "사람이 승인하기도 전에 개발이 나간다"
    이고, 그건 게이트가 아예 없는 것과 같다.
    """
    rid = "REQ-GATE-LIVE-2"
    await purge(rid)
    await reset_agents(SCENARIO)
    await _start(rid, "회의실 예약")

    first = await wait_for(
        rid,
        lambda r: r.state is RequirementState.BLOCKED,
        what="승인 대기(blocked)",
    )

    # 리컨실러 주기(기본 stale/stuck 상한보다 훨씬 짧다)를 여러 번 넘기도록
    # 기다린다. `wait_for` 를 "바뀌면 참" 술어로 뒤집어 쓰면 바뀌는 즉시
    # 실패를 알 수 있지만, 여기서는 **바뀌지 않음**을 보려는 것이므로 타임아웃
    # 자체가 성공 신호다 — 그래서 직접 기다린 뒤 다시 관측한다.
    import asyncio

    await asyncio.sleep(30)

    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{ORCHESTRATOR_URL}/requirements/{rid}")
        resp.raise_for_status()
        assert resp.json()["state"] == RequirementState.BLOCKED.value

    still = await wait_for(
        rid, lambda r: r.state is RequirementState.BLOCKED, what="여전히 blocked"
    )
    assert [t.agent for t in still.tasks] == ["planner"]
    assert still.task_count == first.task_count  # 새 Task 가 생기지 않았다.

    # 뒤따르는 시험에 남기지 않도록 풀고 끝낸다.
    await _approve(rid)
    await wait_for(rid, lambda r: r.state is RequirementState.ACCEPTED, what="accepted")


async def test_approve_is_idempotent_over_http() -> None:
    """승인 버튼을 두 번 눌러도 개발이 두 번 나가지 않는다.

    더블클릭·새로고침·재전송은 사람이 누르는 버튼의 일상이다. 두 번째 호출은
    409 가 아니라 200 + `granted: false` 로 현재 상태를 그대로 돌려준다.
    """
    rid = "REQ-GATE-LIVE-3"
    await purge(rid)
    await reset_agents(SCENARIO)
    await _start(rid, "회의실 예약")
    await wait_for(rid, lambda r: r.state is RequirementState.BLOCKED, what="blocked")

    first = await _approve(rid)
    second = await _approve(rid)
    assert first["granted"] == "true"
    assert second["granted"] == "false"

    done = await wait_for(
        rid, lambda r: r.state is RequirementState.ACCEPTED, what="accepted"
    )
    assert [t.agent for t in done.tasks].count("dev") == 1


async def test_approve_on_unknown_requirement_is_404() -> None:
    """없는 요구사항에 승인을 걸면 500 이 아니라 404 다."""
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{ORCHESTRATOR_URL}/requirements/REQ-NOPE-404/approve")
    assert resp.status_code == 404
