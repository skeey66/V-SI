"""개발. 인수 테스트를 통과시키는 구현을 쓴다 — 테스트 파일은 도구 서버가 거부한다."""
from __future__ import annotations

from lg_agent.deps import Deps
from lg_agent.events import emit
from lg_agent.nodes.common import run_agent
from lg_agent.state import VsiState
from lg_agent.roles import ROLES, system_prompt, task_prompt

#: 반려가 있을 때 개발 프롬프트 끝에 붙인다. 지적들이 서로 부딪히는 경우(보안은
#: md5 를 금지, 테스트는 md5 값을 기대)에 한쪽만 고쳐 진동하는 것을 막는다.
_REMEDIATE_RULES = (
    "\n\n위 지적은 모두 동시에 만족해야 한다. 한쪽을 고치느라 다른 쪽을 깨면 같은 반려가 "
    "반복된다. 인수 테스트의 기대값은 바꿀 수 없다 — 보안 지적은 테스트가 기대하는 출력을 "
    "그대로 유지하는 방법(조치 가이드의 대안)으로 고쳐라."
)


def make_dev(deps: Deps):
    async def dev(state: VsiState) -> dict:
        revision = state.get("revision", 1)
        emit({"type": "agent_start", "agent": "dev", "revision": revision})
        async with deps.tools("dev", state["requirement_id"]) as rt:
            summary = await run_agent(
                deps, name="dev",
                system_prompt=system_prompt(ROLES["dev"]),
                user_prompt=_dev_prompt(state, revision),
                tools=rt.tools,
            )
        return {
            "status": "verifying",
            "history": [{"revision": revision, "agent": "dev", "summary": summary}],
        }

    return dev


def _dev_prompt(state: VsiState, revision: int) -> str:
    feedback = state.get("feedback", [])
    prompt = task_prompt(state["title"], revision, feedback)
    return prompt + _REMEDIATE_RULES if feedback else prompt
