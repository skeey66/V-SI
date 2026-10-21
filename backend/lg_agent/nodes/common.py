"""노드들이 공유하는 ReAct 에이전트 실행기."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError

from lg_agent.deps import Deps


async def run_agent(
    deps: Deps,
    *,
    name: str,
    system_prompt: str,
    user_prompt: str,
    tools: Sequence[BaseTool],
) -> str:
    """`create_agent` 로 ReAct 루프를 한 번 돌리고 마지막 응답 텍스트를 돌려준다.

    상한은 두 겹이다. 턴 상한은 `recursion_limit` (모델 호출 + 도구 실행이 한
    턴에 노드 2개), 시간 상한은 `asyncio.wait_for`. 어느 쪽에 걸려도 예외로
    그래프를 죽이지 않고 텍스트로 돌려준다 — 판정은 이 텍스트가 아니라 다음
    노드의 도구 종료코드가 내리므로, 여기서 멈춘 개발은 QA 에서 FAIL 로 잡힌다.
    """
    agent = create_agent(deps.llm, list(tools), system_prompt=system_prompt, name=name)
    try:
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(user_prompt)]},
                config={"recursion_limit": deps.max_turns * 2 + 1, "run_name": name},
            ),
            timeout=deps.agent_budget_s,
        )
    except TimeoutError:
        return f"[{name}] 작업 예산 {deps.agent_budget_s}초를 넘겨 중단했다"
    except GraphRecursionError:
        return f"[{name}] 턴 상한 {deps.max_turns}회를 넘겨 중단했다"

    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage) and message.content:
            return str(message.content)
    return ""
