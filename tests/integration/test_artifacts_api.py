"""산출물 조회 API — 3D 사무실이 "이 팀이 무엇을 냈는가"를 보여주는 근거.

목록과 내용을 나눈 이유가 이 시험의 요점이다: `content["files"]` 는 산출물
하나당 200KB 까지 가므로(`llm_agent.loop.FILE_SNAPSHOT_TOTAL_CAP_BYTES`),
목록에 파일을 실으면 화면 첫 로드가 무거워진다. 목록은 **파일 이름만** 주고
내용은 사람이 그 팀을 클릭할 때 받는다.

전제: `docker compose up -d --build` 로 스택이 떠 있어야 한다.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.harness import ORCHESTRATOR_URL, run_scenario

SCENARIO = "scenarios/all_pass.yaml"


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


async def test_listing_artifacts_names_every_producer_without_shipping_files() -> None:
    rid = "REQ-ART-list"
    await run_scenario(SCENARIO, rid, "회원가입")

    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{ORCHESTRATOR_URL}/requirements/{rid}/artifacts")
    resp.raise_for_status()
    rows = resp.json()

    assert {r["kind"] for r in rows} == {
        "requirements", "source_code", "test_report", "security_report",
    }
    # 목록은 파일 **이름만** 준다 — 내용 키가 아예 없어야 한다.
    assert all("files" not in r for r in rows)
    assert all("file_names" in r for r in rows)

    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["test_report"]["verdict"] == "PASS"
    assert by_kind["security_report"]["verdict"] == "PASS"
    # 비검증자는 verdict 가 없다(스텁은 planner 에 verdict 를 안 싣는다).
    assert by_kind["source_code"]["verdict"] is None


async def test_fetching_one_artifact_returns_its_files() -> None:
    rid = "REQ-ART-detail"
    await run_scenario(SCENARIO, rid, "회원가입")

    async with httpx.AsyncClient(timeout=30) as c:
        listing = (await c.get(f"{ORCHESTRATOR_URL}/requirements/{rid}/artifacts")).json()
        row = next(r for r in listing if r["kind"] == "requirements")
        detail = await c.get(
            f"{ORCHESTRATOR_URL}/requirements/{rid}/artifacts/{row['kind']}/{row['version']}"
        )
    detail.raise_for_status()
    body = detail.json()

    assert body["kind"] == "requirements"
    assert body["version"] == row["version"]
    # 스텁은 파일을 쓰지 않는다 — 그래도 모양은 dict 여야 한다(없음과 빈 것을
    # 화면이 같은 코드로 다룰 수 있게).
    assert isinstance(body["files"], dict)
    assert sorted(body["files"]) == row["file_names"]


async def test_unknown_requirement_is_404_not_an_empty_list() -> None:
    """오타 난 ID 에 빈 배열을 주면 화면이 "아직 아무것도 안 나왔다"로 읽는다."""
    async with httpx.AsyncClient(timeout=30) as c:
        listing = await c.get(f"{ORCHESTRATOR_URL}/requirements/REQ-ART-nope/artifacts")
        detail = await c.get(
            f"{ORCHESTRATOR_URL}/requirements/REQ-ART-nope/artifacts/requirements/1"
        )
    assert listing.status_code == 404
    assert detail.status_code == 404


async def test_unknown_artifact_on_a_real_requirement_is_404() -> None:
    rid = "REQ-ART-missing"
    await run_scenario(SCENARIO, rid, "회원가입")
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{ORCHESTRATOR_URL}/requirements/{rid}/artifacts/requirements/99")
    assert resp.status_code == 404
