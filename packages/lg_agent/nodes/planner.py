"""기획 → 게이트 → (필요하면) 사람 승인.

기존 경로의 "목표가 비면 시작하지 않는다"를 그대로 옮긴다. 기획이 쓴 인수
테스트를 구현 없이 돌려 봐서 통과해 버리면 그 테스트는 아무것도 검사하지 않는
것이고, 그대로 개발을 보내면 무엇을 만들어도 통과한다. 그럴 때는 `interrupt()`
로 멈추고 사람을 기다린다 (기존 `BLOCKED` 상태).
"""
from __future__ import annotations

from langgraph.types import interrupt

from lg_agent.deps import Deps
from lg_agent.events import emit
from lg_agent.nodes.common import run_agent
from lg_agent.state import VsiState
from lg_agent.prompts import PLANNER_TOOLS
from llm_agent.roles import ROLES, system_prompt, task_prompt


def make_planner(deps: Deps):
    async def planner(state: VsiState) -> dict:
        emit({"type": "agent_start", "agent": "planner", "revision": 0})
        legacy = deps.options.planner_variant == "legacy"
        async with deps.tools("planner", state["requirement_id"]) as rt:
            summary = await run_agent(
                deps, name="planner",
                system_prompt=system_prompt(ROLES["planner"]) if legacy else deps.prompts.planner_system(),
                user_prompt=task_prompt(state["title"], 1, []),
                tools=rt.tools if legacy else [t for t in rt.tools if t.name in PLANNER_TOOLS],
            )
        return {
            "status": "planned",
            "history": [{"revision": 0, "agent": "planner", "summary": summary}],
        }

    return planner


def make_gate(deps: Deps):
    async def gate(state: VsiState) -> dict:
        # 판정은 모델이 아니라 도구 종료코드가 한다 — 모델을 거치지 않고 직접 부른다.
        async with deps.tools("planner", state["requirement_id"]) as rt:
            result = await rt.call("check_acceptance_tests")
        passed = bool(result.get("ok"))
        return {
            "gate": {"ok": passed, "detail": result.get("detail", "")},
            "status": "implementing" if passed else "blocked",
        }

    return gate


def approval(state: VsiState) -> dict:
    """사람의 승인을 기다린다. `Command(resume={"approved": bool})` 로 재개한다."""
    decision = interrupt({
        "type": "approval_required",
        "requirement_id": state["requirement_id"],
        "detail": state.get("gate", {}).get("detail", ""),
    })
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    return {"status": "implementing" if approved else "escalated"}
