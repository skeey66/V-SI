"""개발. 인수 테스트를 통과시키는 구현을 쓴다 — 테스트 파일은 도구 서버가 거부한다."""
from __future__ import annotations

from lg_agent.deps import Deps
from lg_agent.events import emit
from lg_agent.nodes.common import run_agent
from lg_agent.state import VsiState
from llm_agent.roles import ROLES, system_prompt, task_prompt


def make_dev(deps: Deps):
    async def dev(state: VsiState) -> dict:
        revision = state.get("revision", 1)
        emit({"type": "agent_start", "agent": "dev", "revision": revision})
        async with deps.tools("dev", state["requirement_id"]) as rt:
            summary = await run_agent(
                deps, name="dev",
                system_prompt=system_prompt(ROLES["dev"]),
                user_prompt=task_prompt(state["title"], revision, state.get("feedback", [])),
                tools=rt.tools,
            )
        return {
            "status": "verifying",
            "history": [{"revision": revision, "agent": "dev", "summary": summary}],
        }

    return dev
