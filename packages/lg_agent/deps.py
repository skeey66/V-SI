"""노드가 쓰는 외부 의존성 묶음 (의존성 주입).

노드는 LLM 이나 MCP 서버를 직접 만들지 않고 `Deps` 로 받는다. 그래서 단위
테스트에서는 가짜 LLM·가짜 도구로 그래프 전체를 네트워크 없이 돌릴 수 있고,
실행 환경에서는 `build_deps` 가 진짜 것을 꽂는다.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from functools import partial

from langchain_core.language_models import BaseChatModel

from lg_agent.knowledge import Retriever, mcp_retriever
from lg_agent.mcp_tools import RoleTools, role_tools
from lg_agent.options import RuntimeOptions
from lg_agent.prompts import PromptStore
from lg_agent.settings import Settings

#: (role, requirement_id) → `async with` 로 여는 역할 도구 묶음
ToolProvider = Callable[[str, str], AbstractAsyncContextManager[RoleTools]]


@dataclass(frozen=True)
class Deps:
    llm: BaseChatModel
    tools: ToolProvider
    agent_budget_s: float = 300.0
    #: 에이전트 하나가 도는 최대 턴 수 (`llm_agent/loop.py` 의 MAX_TURNS 와 같다)
    max_turns: int = 12
    prompts: PromptStore = field(default_factory=PromptStore)
    options: RuntimeOptions = field(default_factory=RuntimeOptions)
    #: 보안 조치 가이드 검색 (RAG). None 이면 RAG 없이 돈다.
    knowledge: Retriever | None = None


def build_deps(settings: Settings, *, langsmith_client=None) -> Deps:
    from langchain_openai import ChatOpenAI

    return Deps(
        llm=ChatOpenAI(
            model=settings.model, temperature=0, stream_usage=True,
            # 토큰 단위로 받아야 화면에 실시간으로 흘릴 수 있다 (SSE `token` 이벤트).
            streaming=True,
            # 비용 상한: 응답 하나의 출력 토큰. 파일 하나를 쓰는 데 충분한 크기다.
            max_tokens=settings.max_output_tokens,
        ),
        tools=partial(role_tools, settings.mcp_base_url),
        agent_budget_s=settings.agent_budget_s,
        max_turns=settings.max_turns,
        prompts=PromptStore(langsmith_client),
        options=RuntimeOptions(max_revisions=settings.max_revisions, rag_enabled=settings.rag_enabled),
        knowledge=mcp_retriever(settings.knowledge_url),
    )
