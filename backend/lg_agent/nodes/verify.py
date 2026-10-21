"""검증자(QA·보안). 두 노드는 같은 모양이고 병렬로 돈다.

**판정 도구는 노드가 직접 부른다.** 기존 경로는 모델에게 판정 도구를 맡기고,
모델이 그 도구를 안 부르고 턴을 끝내면 최대 2번 다시 재촉했다(`loop.py` 의
`MAX_VERDICT_NUDGES`). 여기서는 그 실수가 구조적으로 일어날 수 없다 — 판정은
모델 호출 전에 이미 끝나 있다.

모델은 FAIL 일 때만 부른다. 도구 출력과 코드를 읽고 개발이 고칠 수 있는 문장으로
옮기는 일이다. PASS 면 모델을 아예 부르지 않는다 — 토큰과 시간이 그만큼 준다.
"""
from __future__ import annotations

from lg_agent.deps import Deps
from lg_agent.events import emit
from lg_agent.knowledge import bandit_query, pytest_query
from lg_agent.nodes.common import run_agent
from lg_agent.rag import as_context, retrieve
from lg_agent.state import Verdict, VsiState
from lg_agent.roles import ROLES, system_prompt

_EXPLAIN = (
    "판정 도구 `{tool}` 는 이미 실행됐고 결과는 FAIL 이다 (종료코드 {code}).\n"
    "아래 출력과, 필요하면 `read_file` 로 코드를 읽어서, 개발 에이전트가 무엇을 "
    "어떻게 고쳐야 하는지 구체적으로 적어라. 어느 항목이 무엇을 기대했고 실제로 "
    "무엇이 왔는지를 쓴다. 판정을 다시 내리지 마라.\n\n"
    "요구사항: {title}\n\n--- 도구 출력 ---\n{detail}"
)


def make_verifier(deps: Deps, role: str):
    verdict_tool = ROLES[role].verdict_tool
    assert verdict_tool is not None, f"{role} 는 판정 도구가 없다"

    async def verify(state: VsiState) -> dict:
        emit({"type": "agent_start", "agent": role, "revision": state.get("revision", 1)})
        async with deps.tools(role, state["requirement_id"]) as rt:
            result = await rt.call(verdict_tool)
            exit_code = result.get("exit_code")
            passed = exit_code == 0
            if passed:
                summary = "통과"
            else:
                detail = result.get("detail", "")
                guides = await _retrieve_guides(deps, role, detail)
                read_only = [t for t in rt.tools if t.name != verdict_tool]
                prompt = _EXPLAIN.format(
                    tool=verdict_tool, code=exit_code, title=state["title"], detail=detail,
                )
                if guides:
                    prompt += "\n\n--- 조치 가이드 (이 방법대로 고치라고 안내해라) ---\n" + guides
                summary = await run_agent(
                    deps, name=role, system_prompt=system_prompt(ROLES[role]),
                    user_prompt=prompt, tools=read_only,
                )
                if guides:
                    # 모델이 요약하며 빠뜨려도 개발이 가이드 원문을 받도록 그대로 붙인다.
                    summary += "\n\n[조치 가이드]\n" + guides
        verdict: Verdict = {
            "agent": role, "verdict": "PASS" if passed else "FAIL",
            "exit_code": exit_code, "summary": summary,
        }
        return {
            "verdicts": {role: verdict},
            "history": [{"revision": state.get("revision", 1), **verdict}],
        }

    verify.__name__ = role
    return verify


async def _retrieve_guides(deps: Deps, role: str, detail: str) -> str:
    """진단 RAG: FAIL 출력으로 조치 가이드를 검색한다.

    QA 는 pytest 실패 줄(예외 이름, `E   assert ...`)로 `qa` 묶음을, 보안은 bandit 규칙
    ID·문장으로 `security` 묶음을 찾는다. 검색 실패는 판정에 영향을 주지 않는다.
    """
    stage, query = ("qa", pytest_query(detail)) if role == "qa" else ("security", bandit_query(detail))
    hits = await retrieve(deps, agent=role, stage=stage, query=query)
    return as_context(hits)
