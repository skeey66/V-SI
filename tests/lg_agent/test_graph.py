"""그래프 흐름 시험. 가짜 LLM·가짜 도구를 주입해 네트워크 없이 돈다."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.types import Command

from lg_agent.deps import Deps
from lg_agent.graph import build_graph, initial_state
from lg_agent.nodes.judge import judge


class FakeLLM(GenericFakeChatModel):
    """도구를 부르지 않고 바로 답한다. 받은 프롬프트를 기록한다."""

    prompts: list = field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages[-1].content)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def make_llm() -> FakeLLM:
    return FakeLLM(messages=iter(AIMessage(f"답변 {i}") for i in range(1000)), prompts=[])


@dataclass
class FakeTools:
    """판정 도구별로 차례대로 돌려줄 종료코드 대본."""

    script: dict[str, list[int]]
    calls: list[str] = field(default_factory=list)

    def provider(self, role: str, requirement_id: str):
        @asynccontextmanager
        async def _open():
            yield self._role_tools(role)
        return _open()

    def _role_tools(self, role: str):
        outer = self

        class _RT:
            tools: list = []

            async def call(self, name: str, arguments=None) -> dict:
                outer.calls.append(name)
                codes = outer.script[name]
                code = codes.pop(0) if len(codes) > 1 else codes[0]
                return {"ok": code == 0, "exit_code": code, "detail": f"{name} 종료코드 {code}"}

        return _RT()


def deps_with(tools: FakeTools, llm=None) -> Deps:
    return Deps(llm=llm or make_llm(), tools=tools.provider)


CFG = {"configurable": {"thread_id": "t1"}}


async def test_all_pass_is_accepted_in_one_revision():
    tools = FakeTools({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    graph = build_graph(deps_with(tools))
    final = await graph.ainvoke(initial_state("REQ-1", "덧셈 함수"), CFG)
    assert final["status"] == "accepted"
    assert final["revision"] == 1
    assert {v["verdict"] for v in final["verdicts"].values()} == {"PASS"}


async def test_qa_failure_feeds_back_to_dev_then_accepts():
    llm = make_llm()
    tools = FakeTools({"check_acceptance_tests": [0], "run_tests": [1, 0], "run_security_scan": [0]})
    graph = build_graph(deps_with(tools, llm))
    final = await graph.ainvoke(initial_state("REQ-2", "덧셈 함수"), CFG)
    assert final["status"] == "accepted"
    assert final["revision"] == 2
    # 2회차 개발 프롬프트에 QA 의 반려 사유가 들어간다
    dev_prompts = [p for p in llm.prompts if p.startswith("요구사항:") and "반려" in p]
    assert dev_prompts and "[qa]" in dev_prompts[0]


async def test_pass_does_not_call_llm_for_verifiers():
    llm = make_llm()
    tools = FakeTools({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    await build_graph(deps_with(tools, llm)).ainvoke(initial_state("REQ-3", "x"), CFG)
    assert len(llm.prompts) == 2  # 기획 1 + 개발 1, 검증자는 모델을 부르지 않는다


async def test_persistent_failure_escalates_at_limit():
    tools = FakeTools({"check_acceptance_tests": [0], "run_tests": [1], "run_security_scan": [0]})
    graph = build_graph(deps_with(tools))
    final = await graph.ainvoke(initial_state("REQ-4", "x", max_revisions=2), CFG)
    assert final["status"] == "escalated"
    assert final["revision"] == 2
    assert tools.calls.count("run_tests") == 2


@pytest.mark.parametrize("approved, expected", [(True, "accepted"), (False, "escalated")])
async def test_empty_tests_wait_for_human(approved, expected):
    tools = FakeTools({"check_acceptance_tests": [1], "run_tests": [0], "run_security_scan": [0]})
    graph = build_graph(deps_with(tools))
    first = await graph.ainvoke(initial_state("REQ-5", "x"), CFG)
    assert "__interrupt__" in first
    assert first["__interrupt__"][0].value["type"] == "approval_required"
    final = await graph.ainvoke(Command(resume={"approved": approved}), CFG)
    assert final["status"] == expected


def test_judge_missing_verdict_is_not_accepted():
    out = judge({"verdicts": {"qa": {"agent": "qa", "verdict": "PASS", "exit_code": 0, "summary": ""}},
                 "revision": 1, "max_revisions": 3})
    assert out["status"] == "remediating"


async def test_security_failure_attaches_retrieved_guide_to_feedback():
    from dataclasses import replace

    queries = []

    async def fake_retriever(query, k):
        queries.append(query)
        return [{"id": "B602", "title": "shell=True", "score": 1.0, "text": "shell=True 를 빼라"}]

    llm = make_llm()
    tools = FakeTools({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [1, 0]})
    deps = replace(deps_with(tools, llm), knowledge=fake_retriever)
    final = await build_graph(deps).ainvoke(initial_state("REQ-6", "x"), CFG)
    assert final["status"] == "accepted" and final["revision"] == 2
    assert len(queries) == 1
    dev_prompt = [p for p in llm.prompts if p.startswith("요구사항:") and "반려" in p][0]
    assert "shell=True 를 빼라" in dev_prompt


async def test_rag_disabled_skips_retrieval():
    from dataclasses import replace

    from lg_agent.options import RuntimeOptions

    called = []

    async def fake_retriever(query, k):
        called.append(query)
        return []

    tools = FakeTools({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [1, 0]})
    deps = replace(deps_with(tools), knowledge=fake_retriever, options=RuntimeOptions(rag_enabled=False))
    await build_graph(deps).ainvoke(initial_state("REQ-7", "x"), CFG)
    assert called == []


def test_judge_carries_previous_failure_of_now_passing_verifier():
    from lg_agent.nodes.judge import CARRIED_PREFIX, make_judge

    prev = [{"agent": "security", "verdict": "FAIL", "summary": "md5 금지"}]
    state = {
        "verdicts": {
            "qa": {"agent": "qa", "verdict": "FAIL", "exit_code": 1, "summary": "md5 값을 기대"},
            "security": {"agent": "security", "verdict": "PASS", "exit_code": 0, "summary": "통과"},
        },
        "revision": 2, "max_revisions": 3, "feedback": prev,
    }
    out = make_judge(memory=True)(state)
    assert [f["agent"] for f in out["feedback"]] == ["qa", "security"]
    assert out["feedback"][1]["summary"] == CARRIED_PREFIX + "md5 금지"
    # 한 번 더 넘겨도 접두어가 겹치지 않는다
    again = make_judge(memory=True)({**state, "feedback": out["feedback"]})
    assert again["feedback"][1]["summary"].count(CARRIED_PREFIX) == 1
    assert [f["agent"] for f in make_judge(memory=False)(state)["feedback"]] == ["qa"]
