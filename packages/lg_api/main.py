"""V-SI LangGraph API (FastAPI).

    PYTHONPATH=packages:services uvicorn lg_api.main:app --port 8200

`lifespan` 에서 설정·체크포인터·그래프를 **한 번만** 만들어 `app.state` 에 둔다.
요청마다 그래프를 컴파일하거나 LLM 클라이언트를 새로 만들지 않는다.

엔드포인트 계약은 `docs/lg/api-contract.md`.
"""
from __future__ import annotations

import asyncio
import json
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
from lg_api.runs import Run, RunRegistry, drive, graph_config


class NewRequirement(BaseModel):
    title: str = Field(min_length=2, max_length=500)


class Approval(BaseModel):
    approved: bool


class Feedback(BaseModel):
    score: int = Field(ge=0, le=1)
    comment: str | None = None


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
        yield
        for run in app.state.runs.all():
            if run.task and not run.task.done():
                run.task.cancel()


app = FastAPI(title="V-SI LangGraph API", lifespan=lifespan)
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
    run = _run_or_404(request, requirement_id)
    client = request.app.state.langsmith
    if client is None or not run.run_ids:
        raise HTTPException(503, "LangSmith 에 기록할 수 없다")
    await asyncio.to_thread(
        client.create_feedback, run.run_ids[0], key="user_score",
        score=body.score, comment=body.comment,
    )
    return {"ok": True}


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
