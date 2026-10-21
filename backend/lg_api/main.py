"""V-SI LangGraph API (FastAPI).

    PYTHONPATH=backend:mcp_servers uvicorn lg_api.main:app --port 8200

`lifespan` 에서 설정·체크포인터·그래프를 **한 번만** 만들어 `app.state` 에 둔다.
요청마다 그래프를 컴파일하거나 LLM 클라이언트를 새로 만들지 않는다.

엔드포인트 계약은 `docs/lg/api-contract.md`.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from lg_agent.deps import build_deps
from lg_agent.graph import build_graph, create_checkpointer, initial_state
from lg_agent.settings import load_settings
from lg_api import admin
from lg_api.admin import feedback_row
from lg_api.runs import Run, RunRegistry, drive, graph_config, restore_runs


class NewRequirement(BaseModel):
    title: str = Field(min_length=2, max_length=500)


class Approval(BaseModel):
    approved: bool


#: 보완 요청 사유로 고를 수 있는 값. 화면의 선택지와 같다.
FEEDBACK_REASONS = ("요구사항과 다름", "테스트 부족", "코드 품질", "보안 우려", "너무 느림")


class Feedback(BaseModel):
    score: int = Field(ge=0, le=1)  # 1 = 승인, 0 = 보완 요청
    reasons: list[str] = Field(default_factory=list, max_length=len(FEEDBACK_REASONS))
    comment: str | None = Field(default=None, max_length=1000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    langsmith = None
    if settings.tracing:
        from langsmith import Client

        langsmith = Client()
    deps = build_deps(settings, langsmith_client=langsmith)
    async with create_checkpointer(settings.checkpoint_url) as saver:
        app.state.settings = settings
        app.state.langsmith = langsmith
        app.state.deps = deps
        app.state.graph = build_graph(deps, saver)
        app.state.runs = RunRegistry()
        if settings.checkpoint_url:  # 메모리 체크포인터는 재시작하면 비어 있다
            await restore_runs(app.state.runs, saver)
        yield
        for run in app.state.runs.all():
            if run.task and not run.task.done():
                run.task.cancel()


# 문서도 /api 아래에 둔다 — 프런트 개발 서버의 /api 프록시로 같은 주소에서 열린다.
app = FastAPI(
    title="V-SI LangGraph API", lifespan=lifespan,
    docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(admin.router)


def _run_or_404(request: Request, requirement_id: str) -> Run:
    run = request.app.state.runs.get(requirement_id)
    if run is None:
        raise HTTPException(404, f"요구사항이 없다: {requirement_id}")
    return run


def _start(request: Request, run: Run, graph_input) -> None:
    state = request.app.state
    config = graph_config(run, state.deps.prompts.active_commit)
    config["metadata"]["rag"] = state.deps.options.rag_enabled
    run.task = asyncio.create_task(drive(run, state.graph, graph_input, config))


@app.post("/api/requirements", status_code=201)
async def create_requirement(body: NewRequirement, request: Request) -> dict:
    requirement_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"
    run = request.app.state.runs.add(Run(requirement_id=requirement_id, title=body.title))
    await run.publish("run_started", requirement_id=requirement_id, title=body.title)
    opts = request.app.state.deps.options
    _start(request, run, initial_state(requirement_id, body.title, opts.max_revisions))
    return run.summary()


@app.get("/api/requirements")
async def list_requirements(request: Request) -> list[dict]:
    return [r.summary() for r in request.app.state.runs.all()]


@app.get("/api/requirements/{requirement_id}")
async def get_requirement(requirement_id: str, request: Request) -> dict:
    run = _run_or_404(request, requirement_id)
    snapshot = await request.app.state.graph.aget_state({"configurable": {"thread_id": requirement_id}})
    values = snapshot.values or {}
    pending = None
    for task in snapshot.tasks or ():
        for intr in task.interrupts or ():
            pending = {"detail": (intr.value or {}).get("detail", "")}
    base = await _project_url(request)
    return {
        **run.summary(),
        "verdicts": {k: {**v, "revision": run.revision} for k, v in (values.get("verdicts") or {}).items()},
        "history": values.get("history", []),
        "gate": values.get("gate"),
        "pending_approval": pending,
        "langsmith_urls": [f"{base}/r/{rid}?trace_id={rid}" for rid in run.run_ids] if base else [],
    }


async def _project_url(request: Request) -> str | None:
    """LangSmith 프로젝트 화면 주소. 한 번 조회해 캐시한다."""
    state = request.app.state
    if getattr(state, "project_url", None) or state.langsmith is None:
        return getattr(state, "project_url", None)
    try:
        project = await asyncio.to_thread(state.langsmith.read_project, project_name=state.settings.langsmith_project)
        state.project_url = f"https://smith.langchain.com/o/{project.tenant_id}/projects/p/{project.id}"
    except Exception:
        return None
    return state.project_url


@app.get("/api/requirements/{requirement_id}/events")
async def events(requirement_id: str, request: Request, after: int = 0) -> StreamingResponse:
    """SSE. 지난 이벤트를 재생한 뒤 실시간으로 이어 보낸다."""
    run = _run_or_404(request, requirement_id)

    async def stream():
        async for event in run.subscribe(after=after):
            if await request.is_disconnected():
                return
            if event is None:
                yield ": ping\n\n"
                continue
            data = json.dumps(event, ensure_ascii=False)
            yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.post("/api/requirements/{requirement_id}/approve", status_code=202)
async def approve(requirement_id: str, body: Approval, request: Request) -> dict:
    run = _run_or_404(request, requirement_id)
    if run.status != "blocked" or (run.task and not run.task.done()):
        raise HTTPException(409, "승인을 기다리는 상태가 아니다")
    await run.publish("resumed", approved=body.approved)
    _start(request, run, Command(resume={"approved": body.approved}))
    return {"ok": True}


@app.post("/api/requirements/{requirement_id}/resume", status_code=202)
async def resume(requirement_id: str, request: Request) -> dict:
    """프로세스가 죽어 멈춘 실행을 체크포인트에서 이어간다.

    LangGraph 는 노드가 끝날 때마다 상태를 체크포인터(Postgres)에 남긴다. 죽은 시점에
    돌던 노드는 처음부터 다시 돌고, 이미 끝난 노드는 다시 돌지 않는다. 기존 경로의
    리컨실러처럼 **자동으로** 다시 시작하지는 않는다 — 이 엔드포인트를 불러야 한다.
    재시작 전의 SSE 이벤트 기록은 메모리에만 있었으므로 사라진다.
    """
    state = request.app.state
    run = state.runs.get(requirement_id)
    if run and run.task and not run.task.done():
        raise HTTPException(409, "이미 실행 중이다")
    snapshot = await state.graph.aget_state({"configurable": {"thread_id": requirement_id}})
    if not snapshot.values:
        raise HTTPException(404, f"체크포인트가 없다: {requirement_id}")
    if not snapshot.next:
        raise HTTPException(409, f"이미 끝났다: {snapshot.values.get('status')}")
    if any(t.interrupts for t in snapshot.tasks):
        raise HTTPException(409, "승인을 기다리는 중이다 — /approve 를 써라")
    if run is None:
        values = snapshot.values
        run = state.runs.add(Run(requirement_id=requirement_id, title=values.get("title", ""),
                                 status=values.get("status", "planned"),
                                 revision=values.get("revision", 1)))
    await run.publish("resumed", approved=None, from_checkpoint=True, next=list(snapshot.next))
    _start(request, run, None)
    return {"ok": True, "next": list(snapshot.next)}


@app.get("/api/requirements/{requirement_id}/files")
async def files(requirement_id: str, request: Request) -> list[dict]:
    _run_or_404(request, requirement_id)
    async with request.app.state.deps.tools("dev", requirement_id) as rt:
        result = await rt.call("list_files")
    names = [n for n in result.get("detail", "").splitlines()
             if n and "__pycache__" not in n and not n.endswith(".pyc") and not n.startswith(".")
             and n != "(빈 워크스페이스)"]
    return [{"path": n} for n in names]


@app.get("/api/requirements/{requirement_id}/files/{path:path}")
async def file_content(requirement_id: str, path: str, request: Request) -> dict:
    _run_or_404(request, requirement_id)
    async with request.app.state.deps.tools("dev", requirement_id) as rt:
        result = await rt.call("read_file", {"path": path})
    if not result.get("ok"):
        raise HTTPException(404, result.get("detail", "읽을 수 없다"))
    return {"path": path, "content": result["detail"]}


@app.post("/api/requirements/{requirement_id}/feedback")
async def feedback(requirement_id: str, body: Feedback, request: Request) -> dict:
    """검수 의견을 LangSmith 피드백으로 남긴다 (key=`user_score`).

    점수는 `score`, 보완 요청 사유는 `value`, 자유 의견은 `comment` 에 담는다 —
    LangSmith 화면과 관리자 화면에서 그대로 걸러 볼 수 있다.
    """
    run = _run_or_404(request, requirement_id)
    client = request.app.state.langsmith
    if client is None or not run.run_ids:
        raise HTTPException(503, "LangSmith 에 기록할 수 없다")
    unknown = [r for r in body.reasons if r not in FEEDBACK_REASONS]
    if unknown:
        raise HTTPException(422, f"알 수 없는 사유: {unknown}")
    reasons = [] if body.score == 1 else body.reasons  # 승인에는 보완 사유가 없다
    comment = (body.comment or "").strip() or None
    await asyncio.to_thread(
        client.create_feedback, run.run_ids[0], key="user_score", score=body.score,
        value={"reasons": reasons} if reasons else None, comment=comment,
    )
    run.feedback = {"score": body.score, "reasons": reasons, "comment": comment, "created_at": time.time()}
    return run.feedback


@app.get("/api/requirements/{requirement_id}/feedback")
async def get_feedback(requirement_id: str, request: Request) -> dict:
    """이미 남긴 검수 의견. 새로고침해도 도장이 그대로 보이게 한다."""
    run = _run_or_404(request, requirement_id)
    client = request.app.state.langsmith
    if run.feedback is None and client is not None and run.run_ids:
        items = await asyncio.to_thread(
            lambda: list(client.list_feedback(run_ids=run.run_ids, feedback_key=["user_score"]))
        )
        if items:
            latest = max(items, key=lambda f: f.created_at)
            run.feedback = feedback_row(latest)
    return {"feedback": run.feedback}


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
