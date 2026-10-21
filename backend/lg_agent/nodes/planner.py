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
from lg_agent.rag import as_context, retrieve

#: 테스트 설계 체크리스트는 이 유사도 이상만 쓴다 — 엉뚱한 체크리스트가 잘못된 테스트를 낳는다
TESTDESIGN_MIN_SCORE = 0.55
from lg_agent.roles import ROLES, system_prompt, task_prompt


def make_planner(deps: Deps):
    async def planner(state: VsiState) -> dict:
        emit({"type": "agent_start", "agent": "planner", "revision": 0})
        legacy = deps.options.planner_variant == "legacy"
        user_prompt = task_prompt(state["title"], 1, [])
        # 예방 RAG: 요구사항과 비슷한 테스트 설계 체크리스트를 찾아 함께 준다
        # 보수적으로 쓴다(2개, 높은 임계값). 실측: 체크리스트의 "한글도 테스트하라"를 보고
        # 기획이 회문이 아닌 문자열을 회문이라고 단언하는 테스트를 썼고, 개발은 테스트를 고칠
        # 수 없어 3회차 내내 반려됐다. 장바구니 예시 숫자가 계산기 테스트로 새기도 했다.
        hits = await retrieve(deps, agent="planner", stage="testdesign", query=state["title"],
                              k=2, min_score=TESTDESIGN_MIN_SCORE)
        if hits:
            user_prompt += (
                "\n\n--- 테스트 설계 체크리스트 (참고용 · 사내 QA 가이드) ---\n" + as_context(hits)
                + "\n\n체크리스트는 참고만 해라. 이 요구사항에 실제로 해당하는 경계값만 고르고, "
                "기대값을 손으로 계산해 확신할 수 있는 케이스만 추가해라. 체크리스트의 예시 숫자·문장을 "
                "그대로 쓰지 말고, 요구사항에 없는 동작(예외·반환값)을 지어내지 마라."
            )
        async with deps.tools("planner", state["requirement_id"]) as rt:
            summary = await run_agent(
                deps, name="planner",
                system_prompt=system_prompt(ROLES["planner"]) if legacy else deps.prompts.planner_system(),
                user_prompt=user_prompt,
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
