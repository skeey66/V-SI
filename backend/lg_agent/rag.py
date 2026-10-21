"""에이전트 단계별 RAG.

| 단계 | 묶음 | 질의 | 결과가 들어가는 곳 |
|---|---|---|---|
| `testdesign`   | testdesign   | 요구사항 문장       | 기획 프롬프트 — 경계값·예외 케이스 체크리스트 |
| `securecoding` | securecoding | 요구사항 문장       | 1회차 개발 프롬프트 — 처음부터 안전한 구현 |
| `qa`           | qa           | pytest 실패 줄      | QA 설명 + 다음 회차 개발 피드백 |
| `security`     | security     | bandit 규칙 ID·문장 | 보안 설명 + 다음 회차 개발 피드백 |

앞의 둘은 **예방**(작업 전에 찾는다), 뒤의 둘은 **진단**(실패한 뒤에 찾는다)이다.
검색이 실패해도 작업은 계속된다 — RAG 는 도움일 뿐 판정에 영향을 주지 않는다.
"""
from __future__ import annotations

from lg_agent.deps import Deps
from lg_agent.events import emit

#: 프롬프트에 넣는 청크 하나의 최대 길이 — 토큰을 아끼고 지시가 묻히지 않게
HIT_CHARS = 900
STAGES = ("testdesign", "securecoding", "qa", "security")


def stage_enabled(deps: Deps, stage: str) -> bool:
    opts = deps.options
    return deps.knowledge is not None and opts.rag_enabled and stage in opts.rag_stages


async def retrieve(deps: Deps, *, agent: str, stage: str, query: str, k: int = 3,
                   min_score: float | None = None) -> list[dict]:
    """한 단계의 검색. 결과는 화면(`rag` 이벤트)에도 흘린다."""
    if not stage_enabled(deps, stage) or not query.strip():
        return []
    try:
        hits = await deps.knowledge(query, k, stage)
        if min_score is not None:
            hits = [h for h in hits if h["score"] >= min_score]
    except Exception as exc:
        emit({"type": "rag", "agent": agent, "collection": stage, "query": query[:300], "hits": [],
              "error": str(exc)})
        return []
    emit({
        "type": "rag", "agent": agent, "collection": stage, "query": query[:300],
        "hits": [{"id": h["id"], "title": h["title"], "score": h["score"], "source": h.get("source")}
                 for h in hits],
    })
    return hits


def as_context(hits: list[dict]) -> str:
    """검색 결과를 프롬프트에 넣을 글로. 출처를 함께 적는다."""
    blocks = []
    for h in hits:
        head = h["title"] + (f" › {h['section']}" if h.get("section") not in (None, "", "개요", "전체") else "")
        blocks.append(f"[{head}] (출처: {h.get('source', '-')})\n{h['text'][:HIT_CHARS]}")
    return "\n\n".join(blocks)
