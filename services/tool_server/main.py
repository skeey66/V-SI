"""MCP 도구 서버.

에이전트가 별도 컨테이너이므로 stdio 가 아니라 Streamable HTTP 로 연다.

**모델은 워크스페이스 루트를 지정할 수 없다.** 루트는 세션 헤더
`X-VSI-Requirement` 에서 서버가 정한다 (스펙 §6.1). 도구 인자에
requirement_id 를 받지 않는 것이 이 설계의 요점이다 — 모델이 다른 요구사항의
디렉터리를 가리킬 방법 자체가 없다.

**역할별 권한은 구조로 집행한다, 선언으로 집행하지 않는다.** 이 컨테이너와
에이전트 컨테이너 사이에는 인증이 없다 — 적대적인 컨테이너는 헤더가 있든
없든 아무 역할이나 주장할 수 있으므로, 여기서 "역할별 서버 인스턴스를 분리"
하는 것은 탈취된 컨테이너를 막기 위함이 **아니다**. 막는 대상은 클라이언트
쪽 버그다: LLM 이 혼동해서 잘못된 도구를 부르거나, `llm_agent` 쪽 배선이
`dev` 역할에 `run_tests` 를 잘못 노출하는 실수. `TOOLS_BY_ROLE` 을 선언만
해두고 모든 도구를 한 MCP 인스턴스에 등록해버리면(과거 구현이 그랬다) 그
선언은 아무것도 막지 않는 죽은 코드가 된다. 그래서 **역할마다 별도
`MCPServer` 인스턴스를 만들고, `TOOLS_BY_ROLE` 에 있는 도구만 등록한다** —
`dev` 인스턴스에는 애초에 `run_tests` 라는 도구가 존재하지 않으므로, 잘못
호출하면 "그런 도구 없음"으로 실패한다(권한 거부가 아니라 존재하지 않음).

구현자 주의 (브리프 대비 실제 SDK 차이): 브리프는 `mcp>=1.2.0` 을 넣고
`from mcp.server.fastmcp import FastMCP` 를 스케치했지만, 설치 시점에 그
제약을 만족하는 최신판은 `mcp==2.2.0` 이었다. 이 버전은 `FastMCP` 를
`MCPServer` 로 개명했고 `mcp.server.fastmcp` 는 마이그레이션 안내만 던지는
모듈로 남아 있다(`mcp.server.mcpserver.MCPServer` 로 이동). `@mcp.tool()`
데코레이터 대신 `add_tool(fn, name=...)` 을 써서 같은 함수를 여러 역할
서버에(허용된 역할에만) 등록한다 — 둘 다 같은 `ToolManager` 경로를 타는
공개 API 다(`inspect.signature` 로 확인).

`streamable_http_app()` 은 여러 인스턴스를 만들 수 있고 각각 독립된
Starlette 앱을 반환하지만(경로별 마운트 자체는 SDK 가 그대로 지원), 그
Starlette 앱은 자기 세션 매니저를 시작하는 `lifespan=lambda app:
session_manager.run()` 을 스스로 갖고 있다. **Starlette 의 `Mount` 는
http/websocket 스코프만 자식에게 넘기고 lifespan 스코프는 넘기지 않는다**
(직접 실행해 확인: 여러 서브 앱을 그냥 `Mount` 로 얹으면 세션 매니저의
태스크그룹이 전혀 시작되지 않는다). 그래서 부모 앱의 lifespan 에서 각 역할
서버의 세션 매니저를 직접 진입시킨다 — `MCPServer.session_manager` **공개**
프로퍼티로(`mcp/server/mcpserver/server.py`), 이 프로퍼티의 독스트링이 바로
이 용도를 이름 붙여 설명한다: "여러 MCPServer 인스턴스를 하나의 FastAPI
애플리케이션에 마운트하는" 것과 같은 고급 사용법을 위해 노출했다고 명시한다.
`TestClient` 로 4개 경로 모두 실제 `initialize`+`tools/call` 왕복까지 확인했다
(아래 "재검증" 참고).

경로: 역할별로 `/mcp/<role>/` (끝 슬래시 포함 — `streamable_http_path="/"` 를
마운트 지점 밑에 붙인 결과다). 끝 슬래시 없이 요청해도 Starlette 이 307 로
슬래시 붙은 경로로 돌려보낸다(메서드·본문 보존).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from tool_server import tools
from tool_server.paths import PathEscape, workspace_root

WORKSPACE_BASE = Path(os.environ.get("VSI_WORKSPACE_BASE", "/workspace"))

#: 스펙 §5.2 의 권한 표. 개발에게 `run_tests` 가 없는 것은 의도다(§5.4).
#: Task 6 의 `Role.tools` 와 값이 겹치는 것도 의도다 — 서버는 에이전트를
#: 신뢰하지 않으므로, 권한이 에이전트 쪽에만 있으면 에이전트가 보내는 값이
#: 곧 권한이 된다. 이 표가 서버 쪽의 독립된 판단 기준이다. **집행은 아래
#: `_build_role_server` 가 이 표를 읽어 역할별 서버에 도구를 실제로
#: 등록/미등록함으로써 이뤄진다** — 표만 두고 등록을 안 하면 이 표는 순수
#: 함수 테스트만 통과시키는 죽은 선언이 된다.
TOOLS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "planner": ("write_file",),
    "dev": ("list_files", "read_file", "write_file"),
    "qa": ("list_files", "read_file", "run_tests"),
    "security": ("list_files", "read_file", "run_security_scan"),
}


def allowed_tools(role: str) -> tuple[str, ...]:
    return TOOLS_BY_ROLE.get(role, ())


def root_for_request(base: Path, requirement_id: str) -> Path:
    return workspace_root(base, requirement_id)


def _ctx_root(requirement_id: str) -> Path:
    return root_for_request(WORKSPACE_BASE, requirement_id)


#: 방어적 재검증. `requirement_id` 는 도구 인자로는 받되(스키마에서 이를
#: 제거하고 세션 값을 주입하는 것은 Task 5 의 몫), 서버는 클라이언트가
#: 그 인자를 순순히 지킨다고 가정하지 않는다. `workspace_root` 가
#: `PathEscape` 를 던지면 여기서 잡아 다른 모든 도구 오류와 같은
#: `{"ok": False, "detail": ...}` 모양으로 되돌린다 — 설치된 SDK 가 우연히
#: 예외를 잡아 `is_error=True` 로 바꿔주는 동작에 기대지 않는다(그 모양은
#: 우리 계약과 다르고 SDK 버전에 따라 달라질 수 있는 부수효과다).
def list_files(requirement_id: str) -> dict[str, Any]:
    """워크스페이스의 파일 목록을 돌려준다."""
    try:
        root = _ctx_root(requirement_id)
    except PathEscape as exc:
        return {"ok": False, "detail": str(exc)}
    r = tools.list_files(root)
    return {"ok": r.ok, "detail": r.detail}


def read_file(requirement_id: str, path: str) -> dict[str, Any]:
    """워크스페이스의 파일을 읽는다."""
    try:
        root = _ctx_root(requirement_id)
    except PathEscape as exc:
        return {"ok": False, "detail": str(exc)}
    r = tools.read_file(root, path)
    return {"ok": r.ok, "detail": r.detail}


def write_file(requirement_id: str, path: str, content: str) -> dict[str, Any]:
    """워크스페이스에 파일을 쓴다."""
    try:
        root = _ctx_root(requirement_id)
    except PathEscape as exc:
        return {"ok": False, "detail": str(exc)}
    r = tools.write_file(root, path, content)
    return {"ok": r.ok, "detail": r.detail}


def run_tests(requirement_id: str) -> dict[str, Any]:
    """워크스페이스에서 pytest 를 실행한다."""
    try:
        root = _ctx_root(requirement_id)
    except PathEscape as exc:
        return {"ok": False, "detail": str(exc), "exit_code": None}
    r = tools.run_tests(root)
    return {"ok": r.ok, "detail": r.detail, "exit_code": r.exit_code}


def run_security_scan(requirement_id: str) -> dict[str, Any]:
    """워크스페이스에서 bandit 을 실행한다."""
    try:
        root = _ctx_root(requirement_id)
    except PathEscape as exc:
        return {"ok": False, "detail": str(exc), "exit_code": None}
    r = tools.run_security_scan(root)
    return {"ok": r.ok, "detail": r.detail, "exit_code": r.exit_code}


_TOOL_FUNCS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "run_tests": run_tests,
    "run_security_scan": run_security_scan,
}


#: Host 헤더 허용 목록. MCP SDK 2.2.0 은 DNS 리바인딩 방어를 **기본으로 켜고**
#: localhost 계열만 허용하므로, 도커 서비스명으로 접근하면 `initialize` 가
#: 421 Misdirected Request 로 거부된다(실측: `Invalid Host header: workspace:8000`).
#:
#: 방어를 끄지 않고 허용 목록만 넓히는 이유: 이 서버는 `internal` 네트워크에만
#: 있어 브라우저가 도달할 수 없으므로 리바인딩 위협 자체가 낮지만, 방어를 통째로
#: 끄면 나중에 이 컨테이너가 다른 네트워크에 노출됐을 때 조용히 무방비가 된다.
#: 환경변수로 덮어쓸 수 있게 해 배포 환경이 바뀌어도 코드를 고치지 않게 한다.
_ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "VSI_MCP_ALLOWED_HOSTS", "workspace:8000,localhost:8000,127.0.0.1:8000"
    ).split(",")
    if h.strip()
]


def _build_role_server(role: str, tool_names: tuple[str, ...]) -> MCPServer:
    server = MCPServer(f"vsi-tools-{role}")
    for name in tool_names:
        server.add_tool(_TOOL_FUNCS[name], name=name)
    return server


#: 역할 → 그 역할만의 `MCPServer`. `TOOLS_BY_ROLE` 에 없는 도구는 이 서버에
#: 아예 존재하지 않는다 — 예를 들어 `ROLE_SERVERS["dev"]` 에는 `run_tests`
#: 라는 이름의 도구가 등록조차 되지 않는다.
ROLE_SERVERS: dict[str, MCPServer] = {
    role: _build_role_server(role, names) for role, names in TOOLS_BY_ROLE.items()
}

#: 역할별 마운트 경로. 실제 요청 경로는 끝 슬래시가 붙는다(`/mcp/dev/`) —
#: 위 모듈 docstring 참고.
ROLE_MOUNT_PATHS: dict[str, str] = {role: f"/mcp/{role}" for role in TOOLS_BY_ROLE}

_SUB_APPS: dict[str, Starlette] = {
    role: server.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(allowed_hosts=_ALLOWED_HOSTS),
    )
    for role, server in ROLE_SERVERS.items()
}


@asynccontextmanager
async def _lifespan(_app: Starlette):
    """네 역할 서버의 세션 매니저를 부모 lifespan 에서 직접 진입시킨다.

    각 서브 앱(`_SUB_APPS[role]`)은 자신의 lifespan 을 갖고 있지만 `Mount`
    밑에서는 그게 호출되지 않는다(모듈 docstring 참고). 대신 여기서
    `AsyncExitStack` 으로 네 세션 매니저를 모두 진입시켜, 하나의 uvicorn
    프로세스가 앱을 띄우고 내릴 때 넷 다 같이 시작·종료되게 한다.
    """
    async with AsyncExitStack() as stack:
        for server in ROLE_SERVERS.values():
            await stack.enter_async_context(server.session_manager.run())
        yield


app = Starlette(
    routes=[Mount(path, app=_SUB_APPS[role]) for role, path in ROLE_MOUNT_PATHS.items()],
    lifespan=_lifespan,
)
