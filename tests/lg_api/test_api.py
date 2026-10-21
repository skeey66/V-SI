"""API 시험. 가짜 LLM·가짜 도구를 주입하고 lifespan 없이 app.state 를 직접 채운다."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from lg_agent.graph import build_graph
from lg_api.main import app
from lg_api.runs import RunRegistry
from tests.lg_agent.test_graph import FakeTools, deps_with


def _setup(script: dict) -> FakeTools:
    tools = FakeTools(script)
    deps = deps_with(tools)
    app.state.settings = SimpleNamespace(model="fake", langsmith_project="test")
    app.state.langsmith = None
    app.state.deps = deps
    app.state.graph = build_graph(deps)
    app.state.runs = RunRegistry()
    return tools


def _parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.split("\n\n"):
        data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            events.append(json.loads("".join(data)))
    return events


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _wait_finished(client, rid: str) -> list[dict]:
    run = app.state.runs.get(rid)
    await asyncio.wait_for(run.task, 10)
    resp = await client.get(f"/api/requirements/{rid}/events")
    assert resp.headers["content-type"].startswith("text/event-stream")
    return _parse_sse(resp.text)


async def test_requirement_streams_until_done(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [1, 0], "run_security_scan": [0]})
    created = (await client.post("/api/requirements", json={"title": "덧셈 함수"})).json()
    events = await _wait_finished(client, created["requirement_id"])
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "done"
    assert events[-1]["status"] == "accepted" and events[-1]["revision"] == 2
    # tool_call/tool_result 는 진짜 MCP 어댑터가 내므로 가짜 도구에서는 나오지 않는다
    assert {"agent_start", "gate", "verdict", "token", "node_end"} <= set(types)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_after_skips_seen_events(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    rid = (await client.post("/api/requirements", json={"title": "x 함수"})).json()["requirement_id"]
    all_events = await _wait_finished(client, rid)
    tail = _parse_sse((await client.get(f"/api/requirements/{rid}/events?after=3")).text)
    assert tail == all_events[3:]


async def test_approval_resumes_blocked_run(client):
    _setup({"check_acceptance_tests": [1], "run_tests": [0], "run_security_scan": [0]})
    rid = (await client.post("/api/requirements", json={"title": "빈 테스트"})).json()["requirement_id"]
    await asyncio.wait_for(app.state.runs.get(rid).task, 10)
    detail = (await client.get(f"/api/requirements/{rid}")).json()
    assert detail["status"] == "blocked" and detail["pending_approval"] is not None

    assert (await client.post(f"/api/requirements/{rid}/approve", json={"approved": True})).status_code == 202
    events = await _wait_finished(client, rid)
    assert "interrupt" in [e["type"] for e in events]
    assert events[-1]["type"] == "done" and events[-1]["status"] == "accepted"


async def test_approve_when_not_blocked_is_conflict(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    rid = (await client.post("/api/requirements", json={"title": "x 함수"})).json()["requirement_id"]
    await asyncio.wait_for(app.state.runs.get(rid).task, 10)
    assert (await client.post(f"/api/requirements/{rid}/approve", json={"approved": True})).status_code == 409


async def test_unknown_requirement_is_404(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    assert (await client.get("/api/requirements/REQ-NOPE")).status_code == 404


async def test_config_patch_changes_runtime_options(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    resp = await client.put("/api/admin/config", json={"rag_enabled": False, "max_revisions": 2})
    assert resp.json()["rag_enabled"] is False and resp.json()["max_revisions"] == 2
    assert (await client.put("/api/admin/config", json={"max_revisions": 9})).status_code == 422


async def test_resume_rejects_finished_and_unknown_runs(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    rid = (await client.post("/api/requirements", json={"title": "x 함수"})).json()["requirement_id"]
    await asyncio.wait_for(app.state.runs.get(rid).task, 10)
    assert (await client.post(f"/api/requirements/{rid}/resume")).status_code == 409
    assert (await client.post("/api/requirements/REQ-NOPE/resume")).status_code == 404


class FakeLangSmith:
    """create_feedback / list_feedback 만 흉내 낸다."""

    def __init__(self):
        self.created = []

    def create_feedback(self, run_id, key, score=None, value=None, comment=None, **_):
        from datetime import datetime, timezone

        self.created.append(SimpleNamespace(
            run_id=run_id, key=key, score=score, value=value, comment=comment,
            created_at=datetime.now(timezone.utc),
        ))

    def list_feedback(self, run_ids, feedback_key, **_):
        return [f for f in self.created if str(f.run_id) in run_ids and f.key in feedback_key]


async def test_feedback_records_reasons_and_survives_reload(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    fake = FakeLangSmith()
    app.state.langsmith = fake
    rid = (await client.post("/api/requirements", json={"title": "x 함수"})).json()["requirement_id"]
    await asyncio.wait_for(app.state.runs.get(rid).task, 10)

    body = {"score": 0, "reasons": ["테스트 부족", "코드 품질"], "comment": "  경계값 테스트가 없다 "}
    saved = (await client.post(f"/api/requirements/{rid}/feedback", json=body)).json()
    assert saved["reasons"] == ["테스트 부족", "코드 품질"] and saved["comment"] == "경계값 테스트가 없다"
    assert fake.created[0].value == {"reasons": ["테스트 부족", "코드 품질"]}

    # 메모리 사본이 없어도(재시작) LangSmith 에서 다시 읽는다
    app.state.runs.get(rid).feedback = None
    again = (await client.get(f"/api/requirements/{rid}/feedback")).json()["feedback"]
    assert again["score"] == 0 and again["reasons"] == ["테스트 부족", "코드 품질"]


async def test_feedback_rejects_unknown_reason_and_drops_reasons_on_approval(client):
    _setup({"check_acceptance_tests": [0], "run_tests": [0], "run_security_scan": [0]})
    app.state.langsmith = FakeLangSmith()
    rid = (await client.post("/api/requirements", json={"title": "x 함수"})).json()["requirement_id"]
    await asyncio.wait_for(app.state.runs.get(rid).task, 10)
    bad = await client.post(f"/api/requirements/{rid}/feedback", json={"score": 0, "reasons": ["아무거나"]})
    assert bad.status_code == 422
    ok = (await client.post(f"/api/requirements/{rid}/feedback", json={"score": 1, "reasons": ["코드 품질"]})).json()
    assert ok["score"] == 1 and ok["reasons"] == []
