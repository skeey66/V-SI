"""Before/After 실측.

    PYTHONPATH=backend:mcp_servers python scripts/bench_lg.py planner --reps 2
    PYTHONPATH=backend:mcp_servers python scripts/bench_lg.py rag --reps 2

같은 요구사항 묶음을 두 설정으로 돌려 완료율·회차·지연·토큰·비용을 비교한다.
토큰은 LangChain 사용량 콜백으로 직접 센다(LangSmith 집계는 몇 초 늦게 반영된다).
비용은 gpt-4o-mini 단가(입력 $0.15/M, 출력 $0.60/M)로 계산한다 — LangSmith 가 같은
실행에 매긴 비용과 대조했다(4,149 토큰 → $0.00083).

비용 상한: 실행 하나가 대략 $0.001~0.007 이다. 기본 설정 전체가 $0.15 안쪽이다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from dataclasses import replace
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from lg_agent.deps import build_deps
from lg_agent.graph import build_graph, initial_state
from lg_agent.options import RuntimeOptions
from lg_agent.settings import load_settings

PRICE_IN, PRICE_OUT = 0.15 / 1e6, 0.60 / 1e6

SUITES = {
    # 기획 프롬프트 Before/After — 평범한 요구사항
    "planner": [
        "장바구니에 담긴 상품 금액을 모두 합치고, 합계가 5만원 이상이면 배송비 3000원을 빼주세요",
        "문자열이 회문인지 판별하는 함수 (대소문자와 공백은 무시)",
        "정수 리스트에서 두 번째로 큰 값을 돌려주는 함수, 값이 부족하면 None",
    ],
    # RAG Before/After — bandit 에 걸리기 쉬운 요구사항
    "rag": [
        "사용자가 입력한 파일명을 받아 셸 명령 wc -l 로 그 파일의 줄 수를 세서 정수로 돌려주는 함수",
        "사용자가 입력한 수식 문자열(예: '2+3*4')을 eval 로 계산해 결과를 돌려주는 계산기 함수",
        "비밀번호 문자열을 md5 로 해시해서 16진수 문자열로 돌려주는 함수",
        "random 모듈로 6자리 숫자 인증코드 문자열을 만드는 함수",
        "sqlite3 메모리 DB 의 users 테이블에서 이름으로 사용자를 찾는 함수, 쿼리는 f-string 으로 만든다",
    ],
}

VARIANTS = {
    "planner": {"before": {"planner_variant": "legacy"}, "after": {"planner_variant": "v2"}},
    "rag": {
        "before": {"rag_enabled": False, "feedback_memory": False},
        "after": {"rag_enabled": True, "feedback_memory": False},
        "memory": {"rag_enabled": False, "feedback_memory": True},
        "rag+memory": {"rag_enabled": True, "feedback_memory": True},
    },
}


async def run_one(deps, title: str, label: str, suite: str) -> dict:
    # 도구 서버는 요구사항 ID 를 디렉터리 이름으로 쓰고 영숫자·`-` 외 문자를 거부한다
    rid = f"BENCH-{suite}-{label.replace('+', '-')}-{uuid.uuid4().hex[:6]}"
    graph = build_graph(deps)
    config = {
        "configurable": {"thread_id": rid}, "run_name": "vsi-graph",
        "metadata": {"requirement_id": rid, "title": title, "impl": "langgraph",
                     "bench": suite, "variant": label, "rag": deps.options.rag_enabled},
        "tags": ["bench"],
    }
    started = time.monotonic()
    with get_usage_metadata_callback() as usage:
        try:
            final = await graph.ainvoke(initial_state(rid, title, deps.options.max_revisions), config)
            status, revision = final.get("status"), final.get("revision")
            if "__interrupt__" in final:
                status = "blocked"
        except Exception as exc:
            status, revision = f"error: {type(exc).__name__}", None
    tin = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    tout = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    return {
        "suite": suite, "variant": label, "title": title, "requirement_id": rid,
        "status": status, "revision": revision, "elapsed_s": round(time.monotonic() - started, 1),
        "tokens": tin + tout, "cost": round(tin * PRICE_IN + tout * PRICE_OUT, 6),
    }


def summarize(rows: list[dict]) -> dict:
    accepted = [r for r in rows if r["status"] == "accepted"]
    return {
        "runs": len(rows),
        "accepted": len(accepted),
        "accept_rate": round(len(accepted) / len(rows), 2),
        "avg_revisions": round(statistics.mean(r["revision"] or 0 for r in rows), 2),
        "avg_elapsed_s": round(statistics.mean(r["elapsed_s"] for r in rows), 1),
        "avg_tokens": round(statistics.mean(r["tokens"] for r in rows)),
        "avg_cost": round(statistics.mean(r["cost"] for r in rows), 5),
        "total_cost": round(sum(r["cost"] for r in rows), 4),
    }


async def main(suite: str, reps: int, concurrency: int, only: list[str] | None) -> None:
    settings = load_settings()
    base = build_deps(settings)
    sem = asyncio.Semaphore(concurrency)
    jobs = []
    for label, opts in VARIANTS[suite].items():
        if only and label not in only:
            continue
        deps = replace(base, options=RuntimeOptions(max_revisions=3, **opts))
        for title in SUITES[suite]:
            for _ in range(reps):
                jobs.append((deps, title, label))

    async def guarded(job):
        async with sem:
            row = await run_one(*job, suite)
            print(json.dumps(row, ensure_ascii=False))
            return row

    rows = await asyncio.gather(*(guarded(j) for j in jobs))
    out = Path("docs/lg") / f"bench-{suite}.json"
    if only and out.exists():  # 일부 조건만 다시 돌렸으면 기존 결과에 합친다
        rows = [r for r in json.loads(out.read_text())["rows"] if r["variant"] not in only] + rows
    labels = [label for label in VARIANTS[suite] if any(r["variant"] == label for r in rows)]
    result = {
        "suite": suite, "model": settings.model, "reps": reps,
        "summary": {label: summarize([r for r in rows if r["variant"] == label]) for label in labels},
        "rows": rows,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("suite", choices=SUITES)
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--only", nargs="*", help="이 조건만 돌리고 기존 결과에 합친다")
    a = p.parse_args()
    asyncio.run(main(a.suite, a.reps, a.concurrency, a.only))
