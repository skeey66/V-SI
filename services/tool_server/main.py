"""MCP 도구 서버.

에이전트가 별도 컨테이너이므로 stdio 가 아니라 Streamable HTTP 로 연다.

**모델은 워크스페이스 루트를 지정할 수 없다.** 루트는 세션 헤더
`X-VSI-Requirement` 에서 서버가 정한다 (스펙 §6.1). 도구 인자에
requirement_id 를 받지 않는 것이 이 설계의 요점이다 — 모델이 다른 요구사항의
디렉터리를 가리킬 방법 자체가 없다.

구현자 주의 (브리프 대비 실제 SDK 차이): 브리프는 `mcp>=1.2.0` 을 넣고
`from mcp.server.fastmcp import FastMCP` 를 스케치했지만, 설치 시점에 그
제약을 만족하는 최신판은 `mcp==2.2.0` 이었다. 이 버전은 `FastMCP` 를
`MCPServer` 로 개명했고 `mcp.server.fastmcp` 는 마이그레이션 안내만 던지는
모듈로 남아 있다(`mcp.server.mcpserver.MCPServer` 로 이동). `@mcp.tool()` 과
`streamable_http_app()` (기본 경로 `/mcp`) 는 이름과 시그니처가 그대로다.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from tool_server import tools
from tool_server.paths import workspace_root

WORKSPACE_BASE = Path(os.environ.get("VSI_WORKSPACE_BASE", "/workspace"))

#: 스펙 §5.2 의 권한 표. 개발에게 `run_tests` 가 없는 것은 의도다(§5.4).
#: Task 6 의 `Role.tools` 와 값이 겹치는 것도 의도다 — 서버는 에이전트를
#: 신뢰하지 않으므로, 권한이 에이전트 쪽에만 있으면 에이전트가 보내는 값이
#: 곧 권한이 된다. 이 표가 서버 쪽의 독립된 집행 기준이다.
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


mcp = MCPServer("vsi-tools")


def _ctx_root(requirement_id: str) -> Path:
    return root_for_request(WORKSPACE_BASE, requirement_id)


@mcp.tool()
def list_files(requirement_id: str) -> dict:
    """워크스페이스의 파일 목록을 돌려준다."""
    r = tools.list_files(_ctx_root(requirement_id))
    return {"ok": r.ok, "detail": r.detail}


@mcp.tool()
def read_file(requirement_id: str, path: str) -> dict:
    """워크스페이스의 파일을 읽는다."""
    r = tools.read_file(_ctx_root(requirement_id), path)
    return {"ok": r.ok, "detail": r.detail}


@mcp.tool()
def write_file(requirement_id: str, path: str, content: str) -> dict:
    """워크스페이스에 파일을 쓴다."""
    r = tools.write_file(_ctx_root(requirement_id), path, content)
    return {"ok": r.ok, "detail": r.detail}


@mcp.tool()
def run_tests(requirement_id: str) -> dict:
    """워크스페이스에서 pytest 를 실행한다."""
    r = tools.run_tests(_ctx_root(requirement_id))
    return {"ok": r.ok, "detail": r.detail, "exit_code": r.exit_code}


@mcp.tool()
def run_security_scan(requirement_id: str) -> dict:
    """워크스페이스에서 bandit 을 실행한다."""
    r = tools.run_security_scan(_ctx_root(requirement_id))
    return {"ok": r.ok, "detail": r.detail, "exit_code": r.exit_code}


app = mcp.streamable_http_app()
