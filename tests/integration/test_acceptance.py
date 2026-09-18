"""수용 테스트 — 스펙 10절의 완료 정의 5개를 그대로 고정한다.

전제: `docker compose up -d --build`로 스택 전체(postgres·오케스트레이터·
에이전트 4종·event-gateway·Jaeger·web)가 떠 있어야 한다.

다섯 기준과 이 파일의 시험 대응:

1. 단일 명령으로 환류 2회 후 accepted       → `test_completion_criterion_1_*`
2. Jaeger 단일 트레이스로 전 구간 관찰 가능   → `test_completion_criterion_2_*`
3. 그래프 UI가 Task 이동을 실시간 관찰       → `test_completion_criterion_3_*`
4. 오케스트레이터 SIGKILL 후 재기동해도 완주 → `test_completion_criterion_4_*`
5. LLM 호출 0회, 토큰 0원                    → `test_completion_criterion_5_*`

**3번은 정직하게 다룬다.** 이 프로젝트에는 실제 브라우저를 구동해 화면 픽셀을
확인할 도구가 없다(Playwright/Chrome MCP 같은 것이 이 리포에 배선돼 있지
않다). 그래서 이 파일이 3번에서 증명하는 것은 "React 그래프가 실제로 구독하는
바로 그 WebSocket(`AgentGraph.tsx`의 `ws://localhost:8100/ws`)이, 서로 다른
에이전트의 Task 이동 이벤트를 워크플로 진행에 맞춰 시간차를 두고(=한꺼번에
배치로 보내는 게 아니라) 전달한다"는 것뿐이다. React가 그 메시지를 받아
실제로 DOM에 노드를 그리고 화면이 움직이는지는 이 시험의 범위 밖이다 —
`web/src/useEventStream.test.ts`(vitest, 리듀서 로직)와 이 파일(전달 메커니즘)
을 합쳐도 "사람 눈에 보이는 애니메이션"까지는 증명하지 못한다.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time

import httpx
import pytest
import websockets

from orchestrator.workflow import RequirementState
from tests.integration.harness import (
    REPO_ROOT,
    kill_orchestrator,
    purge,
    restart_orchestrator,
    run_scenario,
    wait_for,
)

QA_FAILS_TWICE = "scenarios/qa_fails_twice.yaml"
JAEGER_URL = "http://localhost:16686"
GATEWAY_WS_URL = "ws://localhost:8100/ws"


@pytest.fixture(scope="module")
def anyio_backend() -> str:  # pragma: no cover - asyncio_mode=auto용 안전장치
    return "asyncio"


# --------------------------------------------------------- criterion 1


async def test_completion_criterion_1_full_run_with_two_revisions() -> None:
    """단일 명령(`run_scenario` = `scripts/demo.sh`가 두드리는 것과 같은
    `POST /requirements` 한 번)이 환류 2회를 거쳐 accepted에 도달해야 한다.

    `qa_fails_twice.yaml`은 qa가 두 번 FAIL한 뒤 PASS하고 security가 한 번
    FAIL한 뒤 PASS한다 — 이 시나리오가 revision 1(최초 시도)에서 accepted로
    끝나 버리면(예: 재시도 로직이 조기 종료) 이 assert가 깨진다.
    """
    result = await run_scenario(QA_FAILS_TWICE, "REQ-100", "회원가입")
    assert result.state is RequirementState.ACCEPTED
    assert result.revision == 3


# --------------------------------------------------------- criterion 2


async def test_completion_criterion_2_single_trace_in_jaeger() -> None:
    """Jaeger에 5개 서비스가 전부 한 트레이스에 잡혀야 한다.

    `service=orchestrator`만으로 최근 트레이스를 훑으면 도커 헬스체크가
    2초마다 찍는 `GET /healthz` 트레이스가 훨씬 자주 섞여 최근 N개를 다
    차지해 버린다 — `operation=POST /requirements`로 좁혀 워크플로 착수
    스팬만 골라내고, `start`(마이크로초)로 이 시험이 직접 촉발한 실행
    이후만 본다. 이게 끊기면(트레이스 전파가 어느 한 구간에서 새 루트로
    다시 시작되면) service 집합이 5개 미만으로 잡힌다 — Task 14의
    `instrument_app` 호출 순서나 컨텍스트 전파(traceparent 헤더)가 깨졌다는
    뜻이다.

    dev의 `POST /message:send`가 2회 나타나는 것도 함께 고정한다 — 이
    시나리오는 dev가 2회차 호출에서 크래시하므로(Task 11), 같은 트레이스
    안에 "크래시 후 재시도"가 두 번째 스팬으로 남아야 환류가 트레이스에서도
    보인다.
    """
    start_us = int(time.time() * 1_000_000) - 2_000_000  # 여유 2초

    result = await run_scenario(QA_FAILS_TWICE, "REQ-101", "회원가입")
    assert result.state is RequirementState.ACCEPTED

    async with httpx.AsyncClient(base_url=JAEGER_URL, timeout=15) as c:
        r = await c.get(
            "/api/traces",
            params={
                "service": "orchestrator",
                "operation": "POST /requirements",
                "start": start_us,
                "limit": 5,
            },
        )
    r.raise_for_status()
    traces = r.json()["data"]
    assert traces, "Jaeger에 트레이스가 없다 — /requirements 착수 스팬이 기록되지 않았다"

    trace = traces[0]
    processes = {pid: p["serviceName"] for pid, p in trace["processes"].items()}
    services = set(processes.values())
    expected = {"orchestrator", "planner", "dev", "qa", "security"}
    assert expected <= services, f"트레이스가 끊겼다: 있는 서비스={services}, 기대={expected}"

    dev_pids = {pid for pid, name in processes.items() if name == "dev"}
    dev_message_send = [
        s
        for s in trace["spans"]
        if s["operationName"] == "POST /message:send" and s["processID"] in dev_pids
    ]
    assert len(dev_message_send) >= 2, (
        "dev의 POST /message:send가 2회(최초 시도 + 크래시 후 재시도) 이상 "
        f"나타나야 환류가 트레이스에서 보인다: {len(dev_message_send)}회 관측"
    )


# --------------------------------------------------------- criterion 3


async def test_completion_criterion_3_graph_stream_delivers_task_movement_in_realtime() -> None:
    """React 그래프가 구독하는 WS가, 여러 에이전트의 Task 이동을 실시간으로 내보낸다.

    **이 시험이 증명하는 것**: `AgentGraph.tsx`가 실제로 붙는 바로 그 엔드포인트
    (`ws://localhost:8100/ws`)에 워크플로 시작 *전에* 연결해 두면 —

    1. 워크플로가 아직 끝나지 않은 시점에 이미 최소 한 메시지를 받는다
       (`saw_before_run_done`) — 종료 후 일괄 전송이 아니라는 뜻이다.
    2. planner·dev·qa·security 네 에이전트가 모두 스트림에 나타난다.
    3. dev의 `task_submitted`가 2회 이상 나타난다 — qa_fails_twice 시나리오는
       dev가 2회차 호출에서 크래시하므로(리컨실러의 `stale_after_s`=5초 뒤에야
       재디스패치된다), 이 재시도가 스트림에 실제로 보여야 그래프가 "환류"를
       그릴 수 있다.
    4. 첫 메시지와 마지막 메시지 사이 간격이 2초를 뚜렷하게 넘는다 — 이벤트
       게이트웨이의 폴링 주기(0.5초)보다 여유 있게 커서, 전체 실행이 끝난
       뒤 한 사이클에 몰아서 온 게 아니라 리컨실러의 5초 대기를 관통해
       진행에 맞춰 나눠서 도착했음을 보인다. (실측: 이 시나리오의 전체 스트림은
       약 12초에 걸쳐 도착했다 — dev 크래시 복구의 5초 대기가 지배적이다.)

    **이 시험이 증명하지 않는 것**: 브라우저가 이 메시지를 받아 실제로 화면에
    노드를 그리고 움직였다는 것. 이 리포에는 실제 브라우저를 구동해 렌더링
    결과를 확인할 도구가 없다(Playwright·Chrome MCP 같은 것이 이 리포에
    배선돼 있지 않다) — React 쪽 소비 로직은
    `web/src/useEventStream.test.ts`(vitest)가 별도로 리듀서 단위로 고정한다.
    둘을 합쳐도 "사람 눈에 보이는 실시간 애니메이션"까지는 보증하지 못한다.
    """
    rid = "REQ-102"
    await purge(rid)

    ws = await _connect_with_retry()
    received: list[tuple[float, str, dict]] = []  # (수신 시각, event_type, payload)
    saw_before_run_done = False
    try:
        run = asyncio.create_task(run_scenario(QA_FAILS_TWICE, rid, "회원가입"))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 90.0
        while not run.done() and loop.time() < deadline:
            remaining = max(deadline - loop.time(), 0.1)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            payload = msg.get("payload", {})
            if payload.get("requirement_id") != rid:
                continue
            received.append((loop.time(), msg.get("event_type", ""), payload))
            if not run.done():
                saw_before_run_done = True

        # run이 끝난 직후 아직 게이트웨이 폴링 주기를 못 채운 마지막 이벤트가
        # 남아 있을 수 있다 — 짧게 더 받아 스트림 전체를 채운다.
        drain_deadline = loop.time() + 3.0
        while loop.time() < drain_deadline:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(drain_deadline - loop.time(), 0.1)
                )
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            payload = msg.get("payload", {})
            if payload.get("requirement_id") == rid:
                received.append((loop.time(), msg.get("event_type", ""), payload))

        result = await run
    finally:
        await ws.close()

    assert result.state is RequirementState.ACCEPTED
    assert saw_before_run_done, (
        "워크플로가 끝나기 전에는 스트림에서 아무 이벤트도 못 받았다 — "
        "종료 후 일괄 전송처럼 보인다(실시간 전달이 아니다)"
    )

    agents_seen = {p.get("agent") for _, _, p in received if p.get("agent")}
    assert {"planner", "dev", "qa", "security"} <= agents_seen, (
        f"실시간 스트림에 에이전트 Task 이동이 빠졌다: {agents_seen}"
    )

    dev_dispatches = [
        t for t, et, p in received if et == "task_submitted" and p.get("agent") == "dev"
    ]
    assert len(dev_dispatches) >= 2, (
        f"dev 재시도(크래시 후 재디스패치)가 실시간 스트림에서 보이지 않는다: "
        f"{len(dev_dispatches)}회 관측"
    )

    assert received, "이벤트를 하나도 못 받았다"
    spread = received[-1][0] - received[0][0]
    assert spread > 2.0, (
        f"모든 이벤트가 {spread:.2f}초 안에 도착했다 — 진행에 맞춘 실시간 전달이 아니라 "
        "워크플로가 끝난 뒤 한꺼번에 배치로 온 것처럼 보인다"
    )


async def _connect_with_retry(timeout_s: float = 30.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    last_exc: Exception | None = None
    while loop.time() < end:
        try:
            return await websockets.connect(GATEWAY_WS_URL)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            last_exc = exc
            await asyncio.sleep(0.3)
    raise RuntimeError(f"게이트웨이 WS 연결 대기 시간 초과: {last_exc}")


# --------------------------------------------------------- criterion 4


async def test_completion_criterion_4_sigkill_then_restart_completes_without_intervention() -> None:
    """오케스트레이터를 실행 도중 SIGKILL해도, 재기동만으로(사람 개입 없이)
    끝까지(환류 2회 포함) 완주해야 한다.

    Task 13의 `test_reconcile.py`가 이미 여러 킬 지점(디스패치 직후·구현
    중·검증 중·2회차 진입)을 파라미터화해 훨씬 깊게 검증한다 — 여기서는
    수용 기준 문구("SIGKILL 후 재기동해도 완주")를 그대로 재현하는 하나의
    시나리오만 고정해, 이 파일 하나만 읽어도 5개 기준이 전부 보이게 한다.
    """
    rid = "REQ-103"
    await purge(rid)

    run = asyncio.create_task(run_scenario(QA_FAILS_TWICE, rid, "회원가입"))
    try:
        # revision이 2로 오른 시점(첫 환류가 실제로 진행 중) 부근에서 죽인다 —
        # "복구할 게 있는" 상태에서 죽여야 이 시험이 의미가 있다.
        at_kill = await wait_for(
            rid,
            lambda r: r.revision == 2,
            timeout_s=60.0,
            what="2회차 진입",
        )
        assert at_kill.state is not RequirementState.ACCEPTED, "이미 끝난 뒤였다 — 킬이 허공에 떨어졌다"

        await kill_orchestrator()
        await restart_orchestrator()

        result = await run
    finally:
        if not run.done():
            run.cancel()

    assert result.state is RequirementState.ACCEPTED
    assert result.revision == 3


# --------------------------------------------------------- criterion 5


async def test_completion_criterion_5_no_llm_calls() -> None:
    """스텁은 LLM을 호출하지 않는다. 외부 LLM 엔드포인트 의존이 없음을 고정한다.

    브리프 원안은 `packages/`만 훑었다 — 그 뒤로 `services/`(이벤트 게이트웨이)
    와 `web/`(React 그래프)가 생겼으므로 스캔 범위를 넓힌다. `tests/`는 일부러
    뺀다 — 이 시험 자신이 금지어 목록을 문자열 리터럴로 들고 있어(바로 아래
    `forbidden` 튜플) 자기 자신과 매치되어 버린다; 스캔의 목적은 "배포되는
    코드가 LLM을 부르는가"이지 시험 코드의 어휘가 아니다. `web/`은
    `node_modules`를 반드시 빼야 한다(서드파티 라이브러리 소스에 우연히
    "anthropic"·"openai" 문자열이 섞여 들어와도 이 프로젝트의 의존이 아니다).

    **경로는 `__file__`에서 유도한 리포 루트에 고정한다.** 상대 경로
    (`Path("packages")`)는 cwd가 리포 루트일 때만 맞고, 그 밖에서는 `rglob`이
    빈 결과를 내며 **아무것도 읽지 않고 통과**한다. 이 시험의 값어치는 "스캔했고
    없었다"인데 상대 경로는 "스캔하지 않았다"와 구분되지 않는다 — SP1의 정체성
    기준(LLM 0회)을 지키는 시험이 조용히 공회전하면 안 된다. 스캔 대상이
    비어 있지 않다는 것까지 함께 단언한다.
    """
    forbidden = ("api.anthropic.com", "api.openai.com", "anthropic", "openai")

    py_files = [
        p
        for root in ("packages", "services")
        for p in (REPO_ROOT / root).rglob("*.py")
    ]
    assert len(py_files) > 10, f"Python 스캔 대상이 비었다 — 경로가 틀렸다: {py_files}"
    py_text = " ".join(p.read_text(encoding="utf-8") for p in py_files)
    for token in forbidden:
        assert token not in py_text.lower(), f"Python 코드에서 LLM 의존 발견: {token}"

    web_files = [
        p
        for pattern in ("*.ts", "*.tsx")
        for p in (REPO_ROOT / "web" / "src").rglob(pattern)
    ]
    assert web_files, "web/src 스캔 대상이 비었다 — 경로가 틀렸다"
    web_text = " ".join(p.read_text(encoding="utf-8") for p in web_files)
    for token in forbidden:
        assert token not in web_text.lower(), f"web/src에서 LLM 의존 발견: {token}"

    # package.json의 의존성 목록 자체에도 anthropic/openai SDK가 없어야 한다.
    package_json = (
        (REPO_ROOT / "web" / "package.json").read_text(encoding="utf-8").lower()
    )
    for token in ("anthropic", "openai"):
        assert token not in package_json, f"web/package.json에서 LLM 의존 발견: {token}"
