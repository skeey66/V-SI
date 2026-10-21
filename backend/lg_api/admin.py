"""관리자 기능 — LangSmith 를 데이터 원천으로 쓴다.

실행 기록·지연·토큰·비용은 따로 저장하지 않고 LangSmith 가 남긴 트레이스에서
읽는다. 그래프의 루트 실행 이름이 `vsi-graph` 이고 메타데이터에
`requirement_id`·`impl` 이 들어 있으므로 그것으로 거른다.

LangSmith API 는 동기 클라이언트라 `asyncio.to_thread` 로 감싼다 — 이벤트 루프가
막히면 다른 실행의 SSE 가 멈춘다.
"""
from __future__ import annotations

import asyncio
import statistics
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from lg_agent.prompts import PLANNER_PROMPT_NAME, PLANNER_SYSTEM

router = APIRouter(prefix="/api/admin", tags=["admin"])

ROOT_FILTER = 'eq(name, "vsi-graph")'


class ConfigPatch(BaseModel):
    rag_enabled: bool | None = None
    max_revisions: int | None = None


class PromptPush(BaseModel):
    template: str


class PromptActivate(BaseModel):
    commit_hash: str | None = None


def _client(request: Request):
    client = request.app.state.langsmith
    if client is None:
        raise HTTPException(503, "LangSmith 키가 설정되지 않았다")
    return client


def _config(request: Request) -> dict:
    s = request.app.state.settings
    opts = request.app.state.deps.options
    return {
        "model": s.model, "max_revisions": opts.max_revisions, "rag_enabled": opts.rag_enabled,
        "langsmith_project": s.langsmith_project,
        "langsmith_url": "https://smith.langchain.com",
    }


@router.get("/config")
async def get_config(request: Request) -> dict:
    return _config(request)


@router.put("/config")
async def put_config(patch: ConfigPatch, request: Request) -> dict:
    opts = request.app.state.deps.options
    if patch.rag_enabled is not None:
        opts.rag_enabled = patch.rag_enabled
    if patch.max_revisions is not None:
        if not 1 <= patch.max_revisions <= 5:
            raise HTTPException(422, "max_revisions 는 1~5 사이다")
        opts.max_revisions = patch.max_revisions
    return _config(request)


def _root_runs(client, project: str, limit: int, days: int = 7) -> list:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return list(client.list_runs(
        project_name=project, is_root=True, filter=ROOT_FILTER, start_time=since, limit=limit,
    ))


def _row(run, scores: dict[str, float]) -> dict:
    latency = (run.end_time - run.start_time).total_seconds() if run.end_time else None
    meta = run.metadata or {}
    outputs = run.outputs or {}
    return {
        "run_id": str(run.id),
        "requirement_id": meta.get("requirement_id"),
        "title": meta.get("title"),
        "impl": meta.get("impl"),
        "rag": meta.get("rag"),
        "status": outputs.get("status") or ("error" if run.error else run.status),
        "error": run.error,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "latency_s": round(latency, 1) if latency is not None else None,
        "total_tokens": run.total_tokens,
        "total_cost": float(run.total_cost) if run.total_cost is not None else None,
        "url": run.url,
        "feedback_score": scores.get(str(run.id)),
    }


def _feedback_scores(client, run_ids: list[str]) -> dict[str, float]:
    if not run_ids:
        return {}
    scores = {}
    for fb in client.list_feedback(run_ids=run_ids, feedback_key=["user_score"]):
        scores[str(fb.run_id)] = fb.score
    return scores


@router.get("/runs")
async def runs(request: Request, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 100))  # LangSmith 조회 상한이 100 이다
    client = _client(request)
    project = request.app.state.settings.langsmith_project

    def load():
        items = _root_runs(client, project, limit)
        scores = _feedback_scores(client, [str(r.id) for r in items])
        return [_row(r, scores) for r in items]

    return await asyncio.to_thread(load)


@router.get("/summary")
async def summary(request: Request, days: int = 7) -> dict:
    rows = await runs(request, limit=100)
    finished = [r for r in rows if r["latency_s"] is not None]
    latencies = sorted(r["latency_s"] for r in finished)
    costs = [r["total_cost"] or 0.0 for r in rows]
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    decided = [r for r in rows if r["status"] in ("accepted", "escalated", "error")]
    return {
        "runs": len(rows),
        "success_rate": round(by_status.get("accepted", 0) / len(decided), 3) if decided else None,
        "avg_latency_s": round(statistics.mean(latencies), 1) if latencies else None,
        "p95_latency_s": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None,
        "total_tokens": sum(r["total_tokens"] or 0 for r in rows),
        "total_cost": round(sum(costs), 6),
        "avg_cost": round(statistics.mean(costs), 6) if costs else None,
        "by_status": by_status,
        "feedback": {
            "up": sum(1 for r in rows if r["feedback_score"] == 1),
            "down": sum(1 for r in rows if r["feedback_score"] == 0),
        },
    }


def feedback_row(fb) -> dict:
    """LangSmith 피드백 하나 → 화면용 dict (점수 · 보완 사유 · 의견)."""
    value = fb.value if isinstance(fb.value, dict) else {}
    return {
        "score": fb.score, "reasons": value.get("reasons", []), "comment": fb.comment,
        "created_at": fb.created_at.timestamp() if fb.created_at else None,
    }


@router.get("/feedback")
async def feedback_list(request: Request, limit: int = 30) -> list[dict]:
    """최근 검수 의견. 어느 요구사항에 누가 어떤 사유로 보완을 요청했는지 본다."""
    client = _client(request)
    project = request.app.state.settings.langsmith_project

    def load():
        runs = {str(r.id): r for r in _root_runs(client, project, 100)}
        if not runs:
            return []
        items = list(client.list_feedback(run_ids=list(runs), feedback_key=["user_score"]))
        items.sort(key=lambda f: f.created_at, reverse=True)
        out = []
        for fb in items[:limit]:
            run = runs.get(str(fb.run_id))
            meta = (run.metadata or {}) if run else {}
            out.append({
                **feedback_row(fb), "run_id": str(fb.run_id),
                "requirement_id": meta.get("requirement_id"), "title": meta.get("title"),
                "url": run.url if run else None,
            })
        return out

    return await asyncio.to_thread(load)


@router.get("/prompts/planner")
async def planner_prompt(request: Request) -> dict:
    client = _client(request)
    store = request.app.state.deps.prompts

    def load():
        try:
            commits = list(client.list_prompt_commits(PLANNER_PROMPT_NAME, limit=20, include_model=False))
        except Exception:
            commits = []  # 아직 한 번도 push 하지 않았다
        out = []
        for c in commits[:10]:
            # 목록 응답에는 본문이 없다 — 커밋마다 pull 해서 본문을 얻는다 (캐시됨).
            text = store.template_of(c.commit_hash) or ""
            out.append({
                "commit_hash": c.commit_hash,
                "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
                "preview": text[:200],
                "template": text,
            })
        return out

    return {
        "name": PLANNER_PROMPT_NAME, "active_commit": store.active_commit,
        "commits": await asyncio.to_thread(load), "local_default": PLANNER_SYSTEM,
    }


@router.post("/prompts/planner")
async def push_planner_prompt(body: PromptPush, request: Request) -> dict:
    client = _client(request)
    # mustache 형식으로 올린다 — 프롬프트 본문의 `{` `}` 가 변수로 해석되지 않게.
    prompt = ChatPromptTemplate.from_messages([("system", body.template)], template_format="mustache")
    url = await asyncio.to_thread(client.push_prompt, PLANNER_PROMPT_NAME, object=prompt)
    # url 예: https://smith.langchain.com/prompts/vsi-planner/f81e3fbb?organizationId=...
    commit = url.split("?")[0].rstrip("/").split("/")[-1]
    return {"commit_hash": commit, "url": url}


@router.post("/prompts/planner/activate")
async def activate_planner_prompt(body: PromptActivate, request: Request) -> dict:
    store = request.app.state.deps.prompts
    store.active_commit = body.commit_hash or None
    if store.active_commit:
        # 활성화 시점에 미리 받아 둔다 — 실패하면 여기서 알린다.
        text = await asyncio.to_thread(store.template_of, store.active_commit)
        if text is None:
            store.active_commit = None
            raise HTTPException(502, "Prompt Hub 에서 그 버전을 읽지 못했다")
    return {"active_commit": store.active_commit}
