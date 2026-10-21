"""V-SI 협업 그래프.

```
START → planner → gate ─(ok)──────────────▶ dev ─┬─▶ qa ───────┬─▶ judge ─(accepted/escalated)─▶ END
                    └─(빈 테스트)─▶ approval ─┘   └─▶ security ─┘     │
                                   (interrupt)                        └─(remediating)─▶ dev
```

기존 `orchestrator/workflow.py` 의 전이 표와 1:1 로 대응한다:
- `(PLANNED, PLAN_READY) → IMPLEMENTING`          gate ok → dev
- `(PLANNED, APPROVAL_REQUIRED) → BLOCKED`        gate fail → approval
- `(BLOCKED, APPROVAL_GRANTED) → IMPLEMENTING`    approval → dev
- `(VERIFYING, VERDICTS_PASS) → ACCEPTED`         judge → END
- `(VERIFYING, VERDICTS_FAIL) → REMEDIATING`      judge → dev
- `(REMEDIATING, LIMIT_EXCEEDED) → ESCALATED`     judge → END

QA 와 보안은 서로의 결과가 필요 없으므로 병렬로 돈다(fan-out). `judge` 는 둘이
다 끝나야 실행된다(fan-in).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from lg_agent.deps import Deps
from lg_agent.nodes import approval, make_dev, make_gate, make_judge, make_planner, make_verifier
from lg_agent.state import VsiState


def _after_gate(state: VsiState) -> str:
    return "dev" if state.get("status") == "implementing" else "approval"


def _after_approval(state: VsiState) -> str:
    return "dev" if state.get("status") == "implementing" else END


def _after_judge(state: VsiState) -> str:
    return "dev" if state.get("status") == "remediating" else END


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(VsiState)
    g.add_node("planner", make_planner(deps))
    g.add_node("gate", make_gate(deps))
    g.add_node("approval", approval)
    g.add_node("dev", make_dev(deps))
    g.add_node("qa", make_verifier(deps, "qa"))
    g.add_node("security", make_verifier(deps, "security"))
    g.add_node("judge", make_judge(memory=deps.options.feedback_memory))

    g.add_edge(START, "planner")
    g.add_edge("planner", "gate")
    g.add_conditional_edges("gate", _after_gate, ["dev", "approval"])
    g.add_conditional_edges("approval", _after_approval, ["dev", END])
    g.add_edge("dev", "qa")
    g.add_edge("dev", "security")
    g.add_edge(["qa", "security"], "judge")
    g.add_conditional_edges("judge", _after_judge, ["dev", END])

    return g.compile(checkpointer=checkpointer or InMemorySaver())


def initial_state(requirement_id: str, title: str, max_revisions: int = 3) -> VsiState:
    return {
        "requirement_id": requirement_id,
        "title": title,
        "revision": 1,
        "max_revisions": max_revisions,
        "status": "planned",
        "verdicts": {},
        "feedback": [],
        "history": [],
    }


@asynccontextmanager
async def create_checkpointer(url: str | None) -> AsyncIterator[BaseCheckpointSaver]:
    """URL 이 있으면 Postgres, 없으면 메모리. 재기동 후 이어가기는 Postgres 에서만 된다."""
    if not url:
        yield InMemorySaver()
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        yield saver
