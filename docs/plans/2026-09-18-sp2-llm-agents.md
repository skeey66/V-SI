# V-SI SP2 LLM 에이전트 Implementation Plan

> 이 계획은 태스크 단위로 실행하도록 쓰였다. 각 단계는 체크박스(`- [ ]`) 로 추적한다.

**Goal:** 스텁 에이전트 자리에 실제 로컬 LLM 과 MCP 도구 서버를 넣어, 요구사항 하나가 사람 개입 없이 종단 상태에 도달하게 한다. 판정은 LLM 의견이 아니라 `pytest`·`bandit` 종료코드가 내린다.

**Architecture:** `AgentExecutor` 구현을 `StubExecutor` 에서 `LlmExecutor` 로 갈아끼운다. A2A 서버·AgentCard·푸시·Task 저장소는 바뀌지 않는다. `LlmExecutor` 는 Ollama 와 대화하며 MCP 도구를 호출하는 루프를 돌고, 도구는 인터넷 없는 `workspace` 컨테이너 안의 MCP 서버가 제공한다. 오케스트레이터는 디스패치 페이로드를 넓히고 시간 상한을 올린다.

**Tech Stack:** Python 3.11 · Ollama (`qwen3:8b`) · MCP Python SDK (Streamable HTTP) · FastAPI · `a2a-sdk` 1.1.2 · PostgreSQL 16 · pytest · OpenTelemetry · Docker Compose

**Spec:** `docs/specs/2026-09-18-sp2-llm-agents-design.md`

## Global Constraints

- 모델은 네 역할 모두 `qwen3:8b` 하나다. 두 종류를 쓰면 축출·재로드가 반복된다 (스펙 §3).
- 외부 LLM API 호출 0회. `api.anthropic.com`·`api.openai.com` 은 의존성에도 코드에도 없어야 한다 (스펙 §13 기준 6).
- `VSI_AGENT_MODE=stub` 에서 SP1 의 151개 테스트가 그대로 통과해야 한다. 이 층을 깨는 변경은 되돌린다 (스펙 §12.1).
- `verdict` 는 도구 종료코드에서만 온다. 모델 응답 텍스트가 판정에 영향을 주는 경로를 만들지 않는다 (스펙 §5.3).
- 도구를 한 번도 호출하지 않고 끝난 작업은 **실패**다. PASS 도 FAIL 도 아니다 (스펙 §5.3).
- `retry.is_undelivered` 의 범위를 넓히지 않는다. 넓히면 에이전트측 멱등성이 필수가 된다 (스펙 §10.1).
- 에이전트가 쓰는 이벤트(`tool_called`/`tool_result`)는 자문이다. UI 리듀서도 리컨실러도 여기서 상태를 유도하지 않는다 (스펙 §8.1).
- 에이전트는 오케스트레이터의 워크플로 테이블을 조회하지 않는다. 맥락은 A2A 메시지가 실어 나른다 (스펙 §4.3).
- 프롬프트 반복 개선은 범위 밖이다 (스펙 §14.3).
- 턴 상한 12, 에이전트 작업 예산 600초, `stuck_after_s` 900초, `run_s` 3600초 (스펙 §9.2).
- 서브프로세스 타임아웃 60초, 도구 출력 절단 4KB(꼬리 보존), `read_file` 32KB (스펙 §6.2).

---

## 파일 구조

새로 만드는 것:

| 파일 | 책임 |
|---|---|
| `packages/llm_agent/__init__.py` | 패키지 |
| `packages/llm_agent/ollama.py` | Ollama `/api/chat` 클라이언트. 도구 목록 직렬화, 응답 파싱 |
| `packages/llm_agent/mcp_client.py` | MCP Streamable HTTP 클라이언트. 세션 헤더에 요구사항·회차·역할 |
| `packages/llm_agent/roles.py` | 역할별 시스템 프롬프트, 도구 권한, 산출물 `kind` |
| `packages/llm_agent/loop.py` | 대화 루프. 턴 상한·시간 예산·도구 오류 처리 |
| `packages/llm_agent/executor.py` | `LlmExecutor(AgentExecutor)`. A2A 경계 |
| `packages/llm_agent/events.py` | 자문 이벤트(`tool_called`/`tool_result`) 쓰기 |
| `services/tool_server/__init__.py` | 패키지 |
| `services/tool_server/paths.py` | 경로 봉쇄. 루트 해석과 탈출 차단 |
| `services/tool_server/tools.py` | 도구 5종 구현 |
| `services/tool_server/main.py` | MCP 서버 (Streamable HTTP) |

수정하는 것:

| 파일 | 변경 |
|---|---|
| `packages/orchestrator/engine.py` | 디스패치 페이로드 확장 (`title`·`revision`·`feedback`) |
| `packages/orchestrator/reconciler.py` | `run_s` backstop 배선 |
| `packages/orchestrator/policy.py` | 기본값 주석 갱신 |
| `packages/stub_agent/main.py` → `packages/agent_entry/main.py` | 모드 분기 |
| `docker-compose.yml` | `workspace` 서비스, `internal` 네트워크, 환경변수 |
| `Dockerfile` | MCP SDK·pytest·bandit 의존성 |
| `pyproject.toml` | 의존성 |
| `web/src/AgentGraph.tsx`, `useEventStream.ts` | 도구 이벤트 표시 |

---

## Task 1: 경로 봉쇄

워크스페이스 밖으로 나가는 모든 경로를 막는다. 이것이 SP2 의 유일한 보안 경계이므로 제일 먼저 만들고 제일 단단하게 만든다.

**Files:**
- Create: `services/tool_server/__init__.py`, `services/tool_server/paths.py`
- Test: `tests/tool_server/test_paths.py`

**Interfaces:**
- Produces:
  - `class PathEscape(ValueError)` — 루트를 벗어나는 경로
  - `def workspace_root(base: Path, requirement_id: str) -> Path` — 요구사항별 루트를 만들고 반환(없으면 생성)
  - `def resolve_within(root: Path, relative: str) -> Path` — 루트 하위로 해석. 벗어나면 `PathEscape`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/tool_server/test_paths.py
from pathlib import Path

import pytest

from tool_server.paths import PathEscape, resolve_within, workspace_root


def test_normal_relative_path_resolves_under_root(tmp_path: Path) -> None:
    root = workspace_root(tmp_path, "REQ-1")
    assert resolve_within(root, "calc.py") == root / "calc.py"
    assert resolve_within(root, "pkg/mod.py") == root / "pkg" / "mod.py"


def test_parent_escape_is_rejected(tmp_path: Path) -> None:
    root = workspace_root(tmp_path, "REQ-1")
    with pytest.raises(PathEscape):
        resolve_within(root, "../REQ-2/secret.py")


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    root = workspace_root(tmp_path, "REQ-1")
    with pytest.raises(PathEscape):
        resolve_within(root, "/etc/passwd")


def test_symlink_pointing_outside_is_rejected(tmp_path: Path) -> None:
    """심볼릭 링크는 문자열 검사로 못 잡는다 — 해석 후에 검사해야 한다."""
    root = workspace_root(tmp_path, "REQ-1")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    with pytest.raises(PathEscape):
        resolve_within(root, "link.txt")


def test_requirement_ids_get_separate_roots(tmp_path: Path) -> None:
    a = workspace_root(tmp_path, "REQ-1")
    b = workspace_root(tmp_path, "REQ-2")
    assert a != b
    assert a.is_dir() and b.is_dir()


def test_requirement_id_cannot_escape_base(tmp_path: Path) -> None:
    """요구사항 ID 는 SP2 에서 기획 LLM 이 만들 수 있다 — 루트 이름도 봉쇄한다."""
    with pytest.raises(PathEscape):
        workspace_root(tmp_path, "../elsewhere")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/tool_server/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tool_server'`

- [ ] **Step 3: 구현한다**

```python
# services/tool_server/paths.py
"""워크스페이스 경로 봉쇄.

SP2 의 유일한 보안 경계다. LLM 은 워크스페이스 루트를 모르고 상대경로만 쓴다
(스펙 §6.1). 여기서 막지 못하면 생성 코드가 호스트 파일시스템에 닿는다.

문자열 검사로는 부족하다 — 심볼릭 링크는 해석해야 드러난다. 그래서 모든 경로를
`resolve()` 한 뒤 루트 하위인지 본다.
"""
from __future__ import annotations

import re
from pathlib import Path


class PathEscape(ValueError):
    """워크스페이스 루트를 벗어나는 경로."""


#: 요구사항 ID 에 허용하는 문자. SP2 에서는 기획 LLM 이 ID 를 만들 수 있으므로
#: 디렉터리 이름으로 쓰기 전에 좁힌다.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def workspace_root(base: Path, requirement_id: str) -> Path:
    if not _SAFE_ID.match(requirement_id):
        raise PathEscape(f"요구사항 ID 에 쓸 수 없는 문자가 있다: {requirement_id!r}")
    root = (base / requirement_id).resolve()
    if not _is_within(base.resolve(), root):
        raise PathEscape(f"요구사항 루트가 base 를 벗어난다: {requirement_id!r}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not _is_within(root.resolve(), candidate):
        raise PathEscape(f"워크스페이스를 벗어나는 경로다: {relative!r}")
    return candidate


def _is_within(root: Path, candidate: Path) -> bool:
    return root == candidate or root in candidate.parents
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/tool_server/test_paths.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 커밋**

```bash
git add services/tool_server/__init__.py services/tool_server/paths.py tests/tool_server/test_paths.py
git commit -m "feat(tool-server): 워크스페이스 경로 봉쇄"
```

---

## Task 2: 도구 5종

**Files:**
- Create: `services/tool_server/tools.py`
- Test: `tests/tool_server/test_tools.py`

**Interfaces:**
- Consumes: `resolve_within(root, relative) -> Path`, `PathEscape`
- Produces:
  - `@dataclass ToolResult: ok: bool; detail: str; exit_code: int | None = None`
  - `def list_files(root: Path) -> ToolResult`
  - `def read_file(root: Path, path: str) -> ToolResult`
  - `def write_file(root: Path, path: str, content: str) -> ToolResult`
  - `def run_tests(root: Path) -> ToolResult`
  - `def run_security_scan(root: Path) -> ToolResult`
  - 상수: `READ_LIMIT_BYTES = 32_768`, `OUTPUT_TAIL_BYTES = 4_096`, `SUBPROCESS_TIMEOUT_S = 60`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/tool_server/test_tools.py
from pathlib import Path

from tool_server.tools import (
    OUTPUT_TAIL_BYTES,
    READ_LIMIT_BYTES,
    list_files,
    read_file,
    run_security_scan,
    run_tests,
    write_file,
)


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    assert write_file(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n").ok
    result = read_file(tmp_path, "calc.py")
    assert result.ok
    assert "def add" in result.detail


def test_path_escape_returns_error_result_not_exception(tmp_path: Path) -> None:
    """도구 오류는 예외가 아니라 결과다 — 모델이 보고 고쳐야 한다 (스펙 §6.1)."""
    result = write_file(tmp_path, "../escape.py", "x = 1")
    assert not result.ok
    assert "벗어나는" in result.detail


def test_list_files_returns_relative_paths(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "")
    write_file(tmp_path, "pkg/b.py", "")
    result = list_files(tmp_path)
    assert result.ok
    assert "a.py" in result.detail
    assert "pkg/b.py" in result.detail
    assert str(tmp_path) not in result.detail  # 루트를 노출하지 않는다


def test_read_file_truncates_at_limit(tmp_path: Path) -> None:
    write_file(tmp_path, "big.py", "x" * (READ_LIMIT_BYTES + 5_000))
    result = read_file(tmp_path, "big.py")
    assert result.ok
    assert len(result.detail.encode()) <= READ_LIMIT_BYTES + 200  # 절단 표시 여유
    assert "절단" in result.detail


def test_read_missing_file_is_error_result(tmp_path: Path) -> None:
    result = read_file(tmp_path, "nope.py")
    assert not result.ok


def test_run_tests_passes_on_green_suite(tmp_path: Path) -> None:
    write_file(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    write_file(tmp_path, "test_calc.py", "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = run_tests(tmp_path)
    assert result.exit_code == 0
    assert result.ok


def test_run_tests_fails_on_red_suite_and_keeps_tail(tmp_path: Path) -> None:
    write_file(tmp_path, "calc.py", "def add(a, b):\n    return 0\n")
    write_file(tmp_path, "test_calc.py", "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = run_tests(tmp_path)
    assert result.exit_code == 1
    assert not result.ok
    assert len(result.detail.encode()) <= OUTPUT_TAIL_BYTES * 2 + 200
    assert "test_add" in result.detail  # 실패한 테스트 이름이 꼬리에 남는다


def test_run_security_scan_flags_known_bad_pattern(tmp_path: Path) -> None:
    write_file(tmp_path, "bad.py", "import subprocess\nsubprocess.call('ls', shell=True)\n")
    result = run_security_scan(tmp_path)
    assert result.exit_code != 0
    assert not result.ok


def test_run_security_scan_passes_on_clean_code(tmp_path: Path) -> None:
    write_file(tmp_path, "good.py", "def add(a, b):\n    return a + b\n")
    result = run_security_scan(tmp_path)
    assert result.exit_code == 0
    assert result.ok
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/tool_server/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tool_server.tools'`

- [ ] **Step 3: 구현한다**

```python
# services/tool_server/tools.py
"""MCP 가 노출하는 도구 5종.

모든 함수가 예외 대신 `ToolResult` 를 돌려준다. 도구 오류는 모델이 보고 고쳐야
하는 정보이지 프로세스를 죽일 사건이 아니다 (스펙 §6.1).

출력 절단은 보안이 아니라 모델 컨텍스트 보호다 (스펙 §6.2). 8b 모델의 컨텍스트에
pytest 전체 출력을 넣으면 지시가 묻힌다. 꼬리를 남기는 이유는 실패 요약이 끝에
있기 때문이다.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from tool_server.paths import PathEscape, resolve_within

READ_LIMIT_BYTES = 32_768
OUTPUT_TAIL_BYTES = 4_096
SUBPROCESS_TIMEOUT_S = 60


@dataclass
class ToolResult:
    ok: bool
    detail: str
    exit_code: int | None = None


def _tail(text: str, limit: int = OUTPUT_TAIL_BYTES) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    return "...(앞부분 절단)...\n" + raw[-limit:].decode("utf-8", errors="replace")


def list_files(root: Path) -> ToolResult:
    names = sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    )
    return ToolResult(ok=True, detail="\n".join(names) if names else "(빈 워크스페이스)")


def read_file(root: Path, path: str) -> ToolResult:
    try:
        target = resolve_within(root, path)
    except PathEscape as exc:
        return ToolResult(ok=False, detail=str(exc))
    if not target.is_file():
        return ToolResult(ok=False, detail=f"파일이 없다: {path}")
    raw = target.read_bytes()
    if len(raw) > READ_LIMIT_BYTES:
        body = raw[:READ_LIMIT_BYTES].decode("utf-8", errors="replace")
        return ToolResult(ok=True, detail=body + "\n...(뒷부분 절단)...")
    return ToolResult(ok=True, detail=raw.decode("utf-8", errors="replace"))


def write_file(root: Path, path: str, content: str) -> ToolResult:
    try:
        target = resolve_within(root, path)
    except PathEscape as exc:
        return ToolResult(ok=False, detail=str(exc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return ToolResult(ok=True, detail=f"{path} 에 {len(content)} 자를 썼다")


def _run(root: Path, argv: list[str]) -> ToolResult:
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            detail=f"{SUBPROCESS_TIMEOUT_S}초 안에 끝나지 않아 중단했다",
            exit_code=124,
        )
    detail = _tail(proc.stdout) + ("\n--- stderr ---\n" + _tail(proc.stderr) if proc.stderr else "")
    return ToolResult(ok=proc.returncode == 0, detail=detail, exit_code=proc.returncode)


def run_tests(root: Path) -> ToolResult:
    return _run(root, ["python", "-m", "pytest", "-q", "--no-header"])


def run_security_scan(root: Path) -> ToolResult:
    return _run(root, ["python", "-m", "bandit", "-r", ".", "-q"])
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/tool_server/test_tools.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: 커밋**

```bash
git add services/tool_server/tools.py tests/tool_server/test_tools.py
git commit -m "feat(tool-server): 파일·테스트·보안검사 도구 5종"
```

---

## Task 3: MCP 서버

**Files:**
- Create: `services/tool_server/main.py`
- Modify: `pyproject.toml` (의존성 `mcp`, `bandit`), `Dockerfile`
- Test: `tests/tool_server/test_server.py`

**Interfaces:**
- Consumes: Task 2 의 도구 함수들, `workspace_root`
- Produces:
  - MCP 서버. Streamable HTTP, 경로 `/mcp`
  - 세션 헤더 `X-VSI-Requirement`, `X-VSI-Agent` 로 루트와 도구 권한을 정한다
  - `TOOLS_BY_ROLE: dict[str, tuple[str, ...]]` — 역할별 허용 도구

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/tool_server/test_server.py
import pytest

from tool_server.main import TOOLS_BY_ROLE, allowed_tools, root_for_request


def test_each_role_has_the_tools_the_spec_assigns() -> None:
    """스펙 §5.2 의 권한 표를 그대로 고정한다."""
    assert TOOLS_BY_ROLE["planner"] == ("write_file",)
    assert TOOLS_BY_ROLE["dev"] == ("list_files", "read_file", "write_file")
    assert TOOLS_BY_ROLE["qa"] == ("list_files", "read_file", "run_tests")
    assert TOOLS_BY_ROLE["security"] == ("list_files", "read_file", "run_security_scan")


def test_dev_cannot_run_tests() -> None:
    """개발에게 검증 권한을 주지 않는다 (스펙 §5.4)."""
    assert "run_tests" not in allowed_tools("dev")


def test_qa_cannot_write_files() -> None:
    assert "write_file" not in allowed_tools("qa")


def test_unknown_role_gets_no_tools() -> None:
    assert allowed_tools("wat") == ()


def test_root_is_derived_from_header_not_from_tool_arguments(tmp_path) -> None:
    """모델은 루트를 지정할 수 없다 (스펙 §6.1)."""
    root = root_for_request(tmp_path, "REQ-1")
    assert root == (tmp_path / "REQ-1").resolve()


def test_root_rejects_hostile_requirement_id(tmp_path) -> None:
    from tool_server.paths import PathEscape

    with pytest.raises(PathEscape):
        root_for_request(tmp_path, "../../etc")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/tool_server/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tool_server.main'`

- [ ] **Step 3: 의존성을 추가한다**

`pyproject.toml` 의 `dependencies` 에 다음을 추가한다:

```toml
    "mcp>=1.2.0",
    "bandit>=1.7.0",
```

`Dockerfile` 에 `services` 복사가 이미 있으므로 추가 변경은 없다.

- [ ] **Step 4: 구현한다**

```python
# services/tool_server/main.py
"""MCP 도구 서버.

에이전트가 별도 컨테이너이므로 stdio 가 아니라 Streamable HTTP 로 연다.

**모델은 워크스페이스 루트를 지정할 수 없다.** 루트는 세션 헤더
`X-VSI-Requirement` 에서 서버가 정한다 (스펙 §6.1). 도구 인자에
requirement_id 를 받지 않는 것이 이 설계의 요점이다 — 모델이 다른 요구사항의
디렉터리를 가리킬 방법 자체가 없다.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from tool_server import tools
from tool_server.paths import workspace_root

WORKSPACE_BASE = Path(os.environ.get("VSI_WORKSPACE_BASE", "/workspace"))

#: 스펙 §5.2 의 권한 표. 개발에게 `run_tests` 가 없는 것은 의도다(§5.4).
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


mcp = FastMCP("vsi-tools")


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
```

> **구현자 주의.** `requirement_id` 를 도구 인자로 두되 **LLM 에게 노출하는 스키마에서는
> 제거한다** — `llm_agent/mcp_client.py`(Task 5)가 모델에게 도구 목록을 넘길 때 이 인자를
> 빼고, 호출할 때 자기 세션의 값을 주입한다. MCP SDK 1.2 의 `FastMCP` 는 세션 헤더를 도구
> 함수에 직접 주입하는 공식 경로가 없어 이렇게 나눈다. **모델이 이 인자를 볼 수 없다는 것이
> 스펙 §6.1 의 요구이고, 그 집행 지점은 Task 5 다.** 서버는 방어적으로 한 번 더
> `workspace_root` 로 검증한다.

- [ ] **Step 5: 통과를 확인한다**

Run: `pytest tests/tool_server/test_server.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: 커밋**

```bash
git add services/tool_server/main.py tests/tool_server/test_server.py pyproject.toml
git commit -m "feat(tool-server): MCP Streamable HTTP 서버와 역할별 도구 권한"
```

---

## Task 4: Ollama 클라이언트

**Files:**
- Create: `packages/llm_agent/__init__.py`, `packages/llm_agent/ollama.py`
- Test: `tests/llm_agent/test_ollama.py`

**Interfaces:**
- Produces:
  - `@dataclass ToolCall: name: str; arguments: dict`
  - `@dataclass ChatReply: content: str; tool_calls: list[ToolCall]`
  - `class OllamaClient: def __init__(self, base_url: str, model: str, http: httpx.AsyncClient)`
  - `async def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply`
  - `class OllamaUnavailable(RuntimeError)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/llm_agent/test_ollama.py
import httpx
import pytest

from llm_agent.ollama import ChatReply, OllamaClient, OllamaUnavailable


def _client(handler) -> OllamaClient:
    transport = httpx.MockTransport(handler)
    return OllamaClient(
        base_url="http://fake:11434",
        model="qwen3:8b",
        http=httpx.AsyncClient(transport=transport),
    )


async def test_parses_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "write_file",
                                      "arguments": {"path": "calc.py", "content": "x"}}}
                    ],
                }
            },
        )

    reply = await _client(handler).chat([{"role": "user", "content": "hi"}], [])
    assert isinstance(reply, ChatReply)
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "write_file"
    assert reply.tool_calls[0].arguments["path"] == "calc.py"


async def test_parses_plain_content_when_no_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "다 했다"}})

    reply = await _client(handler).chat([], [])
    assert reply.tool_calls == []
    assert reply.content == "다 했다"


async def test_sends_model_and_tools_and_disables_streaming() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": ""}})

    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    await _client(handler).chat([{"role": "user", "content": "x"}], tools)
    assert seen["model"] == "qwen3:8b"
    assert seen["stream"] is False
    assert seen["tools"] == tools


async def test_503_becomes_ollama_unavailable() -> None:
    """모델 로딩 중 503 은 TRANSPORT 로 분류돼야 한다 (스펙 §10)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="loading model")

    with pytest.raises(OllamaUnavailable):
        await _client(handler).chat([], [])


async def test_string_arguments_are_parsed_as_json() -> None:
    """일부 응답은 arguments 를 JSON 문자열로 준다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
            ]}},
        )

    reply = await _client(handler).chat([], [])
    assert reply.tool_calls[0].arguments == {"path": "a.py"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/llm_agent/test_ollama.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_agent'`

- [ ] **Step 3: 구현한다**

```python
# packages/llm_agent/ollama.py
"""Ollama `/api/chat` 클라이언트.

`stream=False` 로 한 번에 받는다. 스트리밍은 UI 에 토큰을 흘릴 때나 필요한데,
여기서 필요한 것은 완성된 `tool_calls` 뿐이다.

`OllamaUnavailable` 을 따로 두는 이유는 모델 로딩 중 503 을 TRANSPORT 로
분류하기 위해서다 (스펙 §10). 새 실패 클래스를 만들지 않고 기존 분류에 얹는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


class OllamaUnavailable(RuntimeError):
    """Ollama 가 응답하지 못한다 — 로딩 중이거나 떠 있지 않다."""


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class OllamaClient:
    def __init__(self, base_url: str, model: str, http: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._http = http

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply:
        body = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            resp = await self._http.post(f"{self._base_url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            raise OllamaUnavailable(f"{resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()

        message = resp.json().get("message", {})
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
        return ChatReply(content=message.get("content", "") or "", tool_calls=calls)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/llm_agent/test_ollama.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add packages/llm_agent/__init__.py packages/llm_agent/ollama.py tests/llm_agent/test_ollama.py
git commit -m "feat(llm-agent): Ollama 채팅 클라이언트와 도구 호출 파싱"
```

---

## Task 5: MCP 클라이언트 — 모델에게서 `requirement_id` 를 숨긴다

스펙 §6.1 의 "모델은 워크스페이스 루트를 모른다"를 집행하는 지점이다.

**Files:**
- Create: `packages/llm_agent/mcp_client.py`
- Test: `tests/llm_agent/test_mcp_client.py`

**Interfaces:**
- Produces:
  - `def tool_schemas(names: Sequence[str], raw: Sequence[dict]) -> list[dict]` — MCP 도구 정의를 Ollama 형식으로 바꾸고 `requirement_id` 를 스키마에서 제거
  - `class ToolBridge: def __init__(self, session, requirement_id: str, allowed: Sequence[str])`
  - `async def call(self, name: str, arguments: dict) -> dict` — 허용 목록 검사 후 `requirement_id` 주입해 호출
  - `class ToolNotAllowed(ValueError)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/llm_agent/test_mcp_client.py
import pytest

from llm_agent.mcp_client import ToolBridge, ToolNotAllowed, tool_schemas

RAW = [
    {"name": "write_file", "description": "쓴다",
     "inputSchema": {"type": "object",
                     "properties": {"requirement_id": {"type": "string"},
                                    "path": {"type": "string"},
                                    "content": {"type": "string"}},
                     "required": ["requirement_id", "path", "content"]}},
    {"name": "run_tests", "description": "돌린다",
     "inputSchema": {"type": "object",
                     "properties": {"requirement_id": {"type": "string"}},
                     "required": ["requirement_id"]}},
]


def test_requirement_id_is_stripped_from_what_the_model_sees() -> None:
    """모델이 워크스페이스를 지정할 수 없어야 한다 (스펙 §6.1)."""
    schemas = tool_schemas(["write_file"], RAW)
    params = schemas[0]["function"]["parameters"]
    assert "requirement_id" not in params["properties"]
    assert "requirement_id" not in params["required"]
    assert "path" in params["properties"]


def test_only_allowed_tools_are_exposed() -> None:
    schemas = tool_schemas(["write_file"], RAW)
    assert [s["function"]["name"] for s in schemas] == ["write_file"]


def test_tool_with_only_requirement_id_still_has_valid_schema() -> None:
    schemas = tool_schemas(["run_tests"], RAW)
    params = schemas[0]["function"]["parameters"]
    assert params["properties"] == {}
    assert params["required"] == []


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return {"ok": True, "detail": "done"}


async def test_bridge_injects_requirement_id_on_call() -> None:
    session = _FakeSession()
    bridge = ToolBridge(session, "REQ-7", ["write_file"])
    await bridge.call("write_file", {"path": "a.py", "content": "x"})
    name, args = session.calls[0]
    assert name == "write_file"
    assert args["requirement_id"] == "REQ-7"
    assert args["path"] == "a.py"


async def test_model_supplied_requirement_id_is_overwritten() -> None:
    """모델이 인자를 지어내도 세션 값이 이긴다."""
    session = _FakeSession()
    bridge = ToolBridge(session, "REQ-7", ["write_file"])
    await bridge.call("write_file", {"requirement_id": "REQ-VICTIM", "path": "a.py", "content": "x"})
    assert session.calls[0][1]["requirement_id"] == "REQ-7"


async def test_tool_outside_allowlist_is_refused() -> None:
    bridge = ToolBridge(_FakeSession(), "REQ-7", ["read_file"])
    with pytest.raises(ToolNotAllowed):
        await bridge.call("run_tests", {})
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/llm_agent/test_mcp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_agent.mcp_client'`

- [ ] **Step 3: 구현한다**

```python
# packages/llm_agent/mcp_client.py
"""MCP 도구를 모델에게 노출하고, 모델의 호출을 서버로 중계한다.

**이 모듈이 스펙 §6.1 의 집행 지점이다.** MCP 서버의 도구는 `requirement_id`
인자를 받지만, 모델에게 주는 스키마에서는 그 인자를 지운다. 호출할 때 세션이
자기 값을 주입한다. 모델이 인자를 지어내더라도 세션 값이 덮어쓴다.

허용 목록도 여기서 집행한다. 서버가 역할별 권한을 알지만, 애초에 모델에게
보이지 않게 하는 편이 낫다 — 모델이 없는 도구를 부르려 시도하는 턴을 아낀다.
"""
from __future__ import annotations

from collections.abc import Sequence

#: 모델에게 절대 노출하지 않는 인자.
_HIDDEN_ARGS = frozenset({"requirement_id"})


class ToolNotAllowed(ValueError):
    """이 역할에 허용되지 않은 도구."""


def tool_schemas(names: Sequence[str], raw: Sequence[dict]) -> list[dict]:
    by_name = {t["name"]: t for t in raw}
    out = []
    for name in names:
        tool = by_name.get(name)
        if tool is None:
            continue
        schema = tool.get("inputSchema", {})
        props = {k: v for k, v in schema.get("properties", {}).items() if k not in _HIDDEN_ARGS}
        required = [r for r in schema.get("required", []) if r not in _HIDDEN_ARGS]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": {"type": "object", "properties": props, "required": required},
                },
            }
        )
    return out


class ToolBridge:
    def __init__(self, session, requirement_id: str, allowed: Sequence[str]) -> None:
        self._session = session
        self._requirement_id = requirement_id
        self._allowed = frozenset(allowed)

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in self._allowed:
            raise ToolNotAllowed(name)
        # 세션 값이 마지막에 들어가 모델이 넣은 값을 덮는다.
        args = {**arguments, "requirement_id": self._requirement_id}
        return await self._session.call_tool(name, args)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/llm_agent/test_mcp_client.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add packages/llm_agent/mcp_client.py tests/llm_agent/test_mcp_client.py
git commit -m "feat(llm-agent): MCP 브리지 — 모델에게서 워크스페이스 식별자를 숨긴다"
```

---

## Task 6: 역할 정의

**Files:**
- Create: `packages/llm_agent/roles.py`
- Test: `tests/llm_agent/test_roles.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `@dataclass Role: name: str; artifact_kind: str; tools: tuple[str, ...]; is_verifier: bool; verdict_tool: str | None`
  - `ROLES: dict[str, Role]`
  - `def system_prompt(role: Role) -> str`
  - `def task_prompt(title: str, revision: int, feedback: list[dict]) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/llm_agent/test_roles.py
from llm_agent.roles import ROLES, system_prompt, task_prompt


def test_four_roles_exist_with_sp1_artifact_kinds() -> None:
    """SP1 의 `kind` 를 그대로 쓴다 — 오케스트레이터가 이 값으로 분기한다."""
    assert ROLES["planner"].artifact_kind == "requirements"
    assert ROLES["dev"].artifact_kind == "source_code"
    assert ROLES["qa"].artifact_kind == "test_report"
    assert ROLES["security"].artifact_kind == "security_report"


def test_only_verifiers_have_a_verdict_tool() -> None:
    assert ROLES["qa"].is_verifier and ROLES["qa"].verdict_tool == "run_tests"
    assert ROLES["security"].is_verifier and ROLES["security"].verdict_tool == "run_security_scan"
    assert not ROLES["dev"].is_verifier and ROLES["dev"].verdict_tool is None
    assert not ROLES["planner"].is_verifier and ROLES["planner"].verdict_tool is None


def test_planner_prompt_demands_acceptance_tests() -> None:
    """기획이 인수 테스트를 쓰는 것이 SP2 의 핵심 결정이다 (스펙 §7)."""
    prompt = system_prompt(ROLES["planner"])
    assert "test_" in prompt
    assert "pytest" in prompt


def test_verifier_prompt_says_it_does_not_decide_the_verdict() -> None:
    prompt = system_prompt(ROLES["qa"])
    assert "판정" in prompt


def test_first_revision_prompt_has_no_feedback_section() -> None:
    prompt = task_prompt("계산기", 1, [])
    assert "계산기" in prompt
    assert "반려" not in prompt


def test_later_revision_prompt_carries_failure_detail_verbatim() -> None:
    """완료 기준 4: 환류가 실제 실패 출력에 근거한다 (스펙 §13)."""
    feedback = [{"agent": "qa", "verdict": "FAIL",
                 "summary": "test_add_negative: assert add(-1,-1) == -2, 실제 0"}]
    prompt = task_prompt("계산기", 2, feedback)
    assert "test_add_negative" in prompt
    assert "실제 0" in prompt


def test_passing_verifiers_are_not_shown_as_complaints() -> None:
    feedback = [
        {"agent": "qa", "verdict": "FAIL", "summary": "테스트 2개 실패"},
        {"agent": "security", "verdict": "PASS", "summary": ""},
    ]
    prompt = task_prompt("계산기", 2, feedback)
    assert "테스트 2개 실패" in prompt
    assert "security" not in prompt
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/llm_agent/test_roles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_agent.roles'`

- [ ] **Step 3: 구현한다**

```python
# packages/llm_agent/roles.py
"""역할별 프롬프트와 권한.

프롬프트 반복 개선은 범위 밖이다 (스펙 §14.3). 여기 있는 문장은 "모델을 잘
구슬리는" 것이 목적이 아니라 **역할의 계약을 적어 두는** 것이 목적이다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    name: str
    artifact_kind: str
    tools: tuple[str, ...]
    is_verifier: bool
    verdict_tool: str | None


ROLES: dict[str, Role] = {
    "planner": Role("planner", "requirements", ("write_file",), False, None),
    "dev": Role("dev", "source_code", ("list_files", "read_file", "write_file"), False, None),
    "qa": Role("qa", "test_report", ("list_files", "read_file", "run_tests"), True, "run_tests"),
    "security": Role(
        "security", "security_report",
        ("list_files", "read_file", "run_security_scan"), True, "run_security_scan",
    ),
}

_PROMPTS = {
    "planner": (
        "너는 기획 에이전트다. 요구사항을 받아 두 가지를 만든다.\n"
        "1. `spec.md` — 무엇을 만들어야 하는지 간결한 명세\n"
        "2. `test_*.py` — 그 명세를 검사하는 pytest 인수 테스트\n\n"
        "인수 테스트가 개발 에이전트의 유일한 목표다. 함수 이름과 시그니처와 "
        "기대값을 테스트에 명확히 박아라. 구현은 하지 마라 — 테스트만 쓴다.\n"
        "테스트는 표준 라이브러리만 쓴다. 외부 패키지를 import 하지 마라."
    ),
    "dev": (
        "너는 개발 에이전트다. 워크스페이스의 인수 테스트를 읽고, 그것을 "
        "통과시키는 구현을 작성한다.\n\n"
        "먼저 `list_files` 와 `read_file` 로 테스트를 읽어라. 테스트 파일은 "
        "수정하지 마라 — 구현 파일만 쓴다.\n"
        "표준 라이브러리만 쓴다. 외부 패키지를 import 하지 마라."
    ),
    "qa": (
        "너는 QA 에이전트다. `run_tests` 로 테스트를 실행한다.\n\n"
        "**판정은 네가 내리지 않는다.** PASS/FAIL 은 pytest 종료코드가 정한다. "
        "네 일은 실패 출력을 개발 에이전트가 고칠 수 있는 문장으로 옮기는 것이다.\n"
        "어느 테스트가 어떤 값을 기대했고 실제로 무엇이 왔는지 구체적으로 적어라."
    ),
    "security": (
        "너는 보안 에이전트다. `run_security_scan` 으로 검사를 실행한다.\n\n"
        "**판정은 네가 내리지 않는다.** PASS/FAIL 은 bandit 종료코드가 정한다. "
        "네 일은 발견된 문제를 개발 에이전트가 고칠 수 있는 문장으로 옮기는 것이다."
    ),
}


def system_prompt(role: Role) -> str:
    return _PROMPTS[role.name]


def task_prompt(title: str, revision: int, feedback: list[dict]) -> str:
    lines = [f"요구사항: {title}"]
    complaints = [f for f in feedback if f.get("verdict") == "FAIL"]
    if complaints:
        lines.append("")
        lines.append(f"직전 회차({revision - 1})가 반려됐다. 사유:")
        for item in complaints:
            lines.append(f"- [{item['agent']}] {item.get('summary', '')}")
        lines.append("")
        lines.append("위 지적을 고쳐라.")
    return "\n".join(lines)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/llm_agent/test_roles.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add packages/llm_agent/roles.py tests/llm_agent/test_roles.py
git commit -m "feat(llm-agent): 역할 정의와 프롬프트"
```

---

## Task 7: 대화 루프

가장 중요한 로직이다. 턴 상한, 시간 예산, 도구 오류 처리, 그리고 **판정이 종료코드에서만 온다**는 규칙이 여기 있다.

**Files:**
- Create: `packages/llm_agent/loop.py`
- Test: `tests/llm_agent/test_loop.py`

**Interfaces:**
- Consumes: `OllamaClient.chat`, `ToolBridge.call`, `Role`, `system_prompt`, `task_prompt`
- Produces:
  - `MAX_TURNS = 12`, `BUDGET_S = 600.0`
  - `@dataclass LoopResult: payload: dict; turns: int`
  - `class LoopFailed(RuntimeError): def __init__(self, reason: str)`
  - `async def run_loop(*, role, llm, bridge, schemas, title, revision, feedback, on_tool=None, now=time.monotonic, max_turns=MAX_TURNS, budget_s=BUDGET_S) -> LoopResult`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/llm_agent/test_loop.py
import pytest

from llm_agent.loop import LoopFailed, run_loop
from llm_agent.ollama import ChatReply, ToolCall
from llm_agent.roles import ROLES


class _ScriptedLlm:
    """각본대로 응답한다. 실제 모델 없이 루프를 결정적으로 시험한다."""

    def __init__(self, replies: list[ChatReply]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    async def chat(self, messages, tools):
        self.seen.append(list(messages))
        return self._replies.pop(0) if self._replies else ChatReply(content="끝")


class _RecordingBridge:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {"ok": True, "detail": "ok"}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


async def test_tool_result_is_fed_back_into_the_conversation() -> None:
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="다 했다"),
    ])
    bridge = _RecordingBridge({"ok": True, "detail": "a.py 에 1 자를 썼다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert bridge.calls[0][0] == "write_file"
    last_messages = llm.seen[-1]
    assert any("a.py 에 1 자를 썼다" in str(m.get("content", "")) for m in last_messages)


async def test_turn_cap_ends_the_task_as_failure() -> None:
    forever = [ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})])] * 50
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=_ScriptedLlm(forever), bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[], max_turns=3,
        )
    assert "턴" in str(exc.value)


async def test_budget_overrun_ends_the_task_as_failure() -> None:
    clock = iter([0.0, 1.0, 700.0, 700.0])
    forever = [ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})])] * 50
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["dev"], llm=_ScriptedLlm(forever), bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[],
            now=lambda: next(clock), budget_s=600.0,
        )
    assert "예산" in str(exc.value)


async def test_tool_error_becomes_a_message_not_an_exception() -> None:
    """모델이 오류를 보고 고칠 기회를 가져야 한다 (스펙 §6.1)."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "../x", "content": "x"})]),
        ChatReply(content="알겠다"),
    ])
    bridge = _RecordingBridge({"ok": False, "detail": "워크스페이스를 벗어나는 경로다"})
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.turns == 2
    assert any("벗어나는" in str(m.get("content", "")) for m in llm.seen[-1])


async def test_verifier_verdict_comes_from_exit_code_only() -> None:
    """모델이 PASS 라고 말해도 종료코드 1이면 FAIL 이다 (스펙 §5.3)."""
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("run_tests", {})]),
        ChatReply(content="전부 통과했다! PASS"),
    ])
    bridge = _RecordingBridge({"ok": False, "detail": "1 failed", "exit_code": 1})
    result = await run_loop(
        role=ROLES["qa"], llm=llm, bridge=bridge, schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert result.payload["exit_code"] == 1
    assert result.payload["verdict"] == "FAIL"


async def test_verifier_that_never_called_its_tool_fails() -> None:
    """근거 없는 판정을 만들지 않는다 (스펙 §5.3)."""
    llm = _ScriptedLlm([ChatReply(content="보아하니 괜찮다")])
    with pytest.raises(LoopFailed) as exc:
        await run_loop(
            role=ROLES["qa"], llm=llm, bridge=_RecordingBridge(),
            schemas=[], title="계산기", revision=1, feedback=[],
        )
    assert "도구" in str(exc.value)


async def test_non_verifier_payload_has_no_verdict() -> None:
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="완료"),
    ])
    result = await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
    )
    assert "verdict" not in result.payload
    assert result.payload["exit_code"] == 0
    assert result.payload["kind"] == "source_code"


async def test_on_tool_callback_fires_for_each_call() -> None:
    """자문 이벤트 발행 지점 (스펙 §8.1)."""
    seen = []
    llm = _ScriptedLlm([
        ChatReply(tool_calls=[ToolCall("write_file", {"path": "a.py", "content": "x"})]),
        ChatReply(content="완료"),
    ])
    await run_loop(
        role=ROLES["dev"], llm=llm, bridge=_RecordingBridge(), schemas=[],
        title="계산기", revision=1, feedback=[],
        on_tool=lambda name, args, result: seen.append((name, result["ok"])),
    )
    assert seen == [("write_file", True)]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/llm_agent/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_agent.loop'`

- [ ] **Step 3: 구현한다**

```python
# packages/llm_agent/loop.py
"""LLM 대화 루프.

**안쪽 상한은 행동할 수 있는 쪽이 집행한다** (스펙 §9.2). 에이전트는 자기가 턴
상한이나 시간 예산을 넘긴 것을 알고 스스로 실패로 끝낸다. 그래서 리컨실러의
`stuck_after_s` 는 "느린 작업"이 아니라 "죽은 프로세스"만 잡는 역할로 돌아간다.

도구 오류는 예외가 아니라 대화 메시지로 들어간다 (스펙 §6.1). 모델이 보고
고칠 기회를 갖는다.

검증자의 `verdict` 는 도구 종료코드에서만 온다 (스펙 §5.3). 모델 응답 텍스트는
`summary` 로만 들어가고 판정에 영향을 주지 않는다 — 이 파일에 그 경로가 없다.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from llm_agent.roles import Role, system_prompt, task_prompt

MAX_TURNS = 12
BUDGET_S = 600.0


class LoopFailed(RuntimeError):
    """작업을 완수하지 못했다. 오케스트레이터의 재시도·환류 상한이 이어받는다."""


@dataclass
class LoopResult:
    payload: dict
    turns: int


async def run_loop(
    *,
    role: Role,
    llm,
    bridge,
    schemas: list[dict],
    title: str,
    revision: int,
    feedback: list[dict],
    on_tool: Callable[[str, dict, dict], None] | None = None,
    now: Callable[[], float] = time.monotonic,
    max_turns: int = MAX_TURNS,
    budget_s: float = BUDGET_S,
) -> LoopResult:
    started = now()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(role)},
        {"role": "user", "content": task_prompt(title, revision, feedback)},
    ]
    verdict_exit_code: int | None = None
    verdict_detail = ""
    turns = 0

    while True:
        if turns >= max_turns:
            raise LoopFailed(f"턴 상한 {max_turns} 을 넘겼다")
        if now() - started > budget_s:
            raise LoopFailed(f"작업 예산 {budget_s}초를 넘겼다")

        reply = await llm.chat(messages, schemas)
        turns += 1

        if not reply.tool_calls:
            break

        messages.append(
            {"role": "assistant", "content": reply.content, "tool_calls": [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls
            ]}
        )
        for call in reply.tool_calls:
            result = await bridge.call(call.name, call.arguments)
            if on_tool is not None:
                on_tool(call.name, call.arguments, result)
            if role.is_verifier and call.name == role.verdict_tool:
                verdict_exit_code = result.get("exit_code")
                verdict_detail = result.get("detail", "")
            messages.append(
                {"role": "tool", "name": call.name,
                 "content": json.dumps(result, ensure_ascii=False)}
            )

    if role.is_verifier and verdict_exit_code is None:
        # 근거 없는 통과를 만드느니 실패시킨다 (스펙 §5.3).
        raise LoopFailed(f"{role.name} 이 판정 도구({role.verdict_tool})를 호출하지 않았다")

    payload: dict = {"kind": role.artifact_kind, "agent": role.name}
    if role.is_verifier:
        payload["exit_code"] = verdict_exit_code
        payload["verdict"] = "PASS" if verdict_exit_code == 0 else "FAIL"
        payload["summary"] = reply.content or verdict_detail
    else:
        payload["exit_code"] = 0
        payload["summary"] = reply.content
    return LoopResult(payload=payload, turns=turns)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/llm_agent/test_loop.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
git add packages/llm_agent/loop.py tests/llm_agent/test_loop.py
git commit -m "feat(llm-agent): 대화 루프 — 턴 상한, 시간 예산, 종료코드 판정"
```

---

## Task 8: 자문 이벤트

**Files:**
- Create: `packages/llm_agent/events.py`
- Test: `tests/llm_agent/test_events.py`

**Interfaces:**
- Consumes: `orchestrator.models.OutboxEvent`
- Produces:
  - `async def record_tool_event(session_maker, *, requirement_id: str, agent: str, revision: int, tool: str, result: dict) -> None`
  - 이벤트 타입 `tool_called` 과 `tool_result` 를 한 번에 쓴다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/llm_agent/test_events.py
import pytest
from sqlalchemy import select

from llm_agent.events import record_tool_event
from orchestrator.models import OutboxEvent


async def test_writes_advisory_event_with_requirement_aggregate(session_maker) -> None:
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="dev", revision=2,
        tool="write_file", result={"ok": True, "detail": "썼다"},
    )
    async with session_maker() as s:
        rows = (await s.execute(select(OutboxEvent).order_by(OutboxEvent.event_id))).scalars().all()
    assert [r.event_type for r in rows] == ["tool_result"]
    assert rows[0].aggregate == "requirement"
    assert rows[0].aggregate_id == "REQ-1"
    assert rows[0].payload["agent"] == "dev"
    assert rows[0].payload["tool"] == "write_file"
    assert rows[0].payload["revision"] == 2
    assert rows[0].payload["ok"] is True


async def test_exit_code_is_carried_when_present(session_maker) -> None:
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="qa", revision=1,
        tool="run_tests", result={"ok": False, "detail": "1 failed", "exit_code": 1},
    )
    async with session_maker() as s:
        row = (await s.execute(select(OutboxEvent))).scalars().one()
    assert row.payload["exit_code"] == 1


async def test_event_carries_no_state_field(session_maker) -> None:
    """자문 이벤트다 — 상태를 유도할 수 있는 필드를 실으면 안 된다 (스펙 §8.1)."""
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="dev", revision=1,
        tool="write_file", result={"ok": True, "detail": "x"},
    )
    async with session_maker() as s:
        row = (await s.execute(select(OutboxEvent))).scalars().one()
    assert "state" not in row.payload
    assert "verdict" not in row.payload


async def test_detail_is_truncated_to_keep_events_small(session_maker) -> None:
    await record_tool_event(
        session_maker, requirement_id="REQ-1", agent="qa", revision=1,
        tool="run_tests", result={"ok": False, "detail": "x" * 5000, "exit_code": 1},
    )
    async with session_maker() as s:
        row = (await s.execute(select(OutboxEvent))).scalars().one()
    assert len(row.payload["detail"]) <= 512
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/llm_agent/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_agent.events'`

구현자 주의: `session_maker` 픽스처는 `tests/integration/harness.py` 의 `db_session()` 패턴을 따라 `tests/llm_agent/conftest.py` 에 만든다. 실제 postgres 를 쓴다.

- [ ] **Step 3: 구현한다**

```python
# packages/llm_agent/events.py
"""에이전트가 쓰는 자문 이벤트.

**이 이벤트에는 워크플로 권위가 없다** (스펙 §8.1). UI 리듀서도 리컨실러도
여기서 상태를 유도해서는 안 된다. 상태의 근거는 언제나 오케스트레이터가 상태
전이와 한 트랜잭션에 쓴 `state_changed` 다.

이 규칙을 깨면 트랜잭셔널 아웃박스의 보장이 무너진다 — 에이전트의 이벤트 쓰기는
워크플로 전이와 원자적이지 않으므로, 이벤트는 있는데 상태는 안 바뀐 창이 존재한다.
그래서 payload 에 `state`·`verdict` 같은 필드를 싣지 않는다.
"""
from __future__ import annotations

from orchestrator.models import OutboxEvent

#: UI 표시용이므로 짧게 자른다. 전체 출력은 아티팩트에 남는다.
_DETAIL_LIMIT = 512


async def record_tool_event(
    session_maker,
    *,
    requirement_id: str,
    agent: str,
    revision: int,
    tool: str,
    result: dict,
) -> None:
    payload = {
        "agent": agent,
        "tool": tool,
        "revision": revision,
        "ok": bool(result.get("ok")),
        "detail": str(result.get("detail", ""))[:_DETAIL_LIMIT],
    }
    if result.get("exit_code") is not None:
        payload["exit_code"] = result["exit_code"]
    async with session_maker() as s:
        s.add(
            OutboxEvent(
                aggregate="requirement",
                aggregate_id=requirement_id,
                event_type="tool_result",
                payload=payload,
            )
        )
        await s.commit()
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/llm_agent/test_events.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add packages/llm_agent/events.py tests/llm_agent/conftest.py tests/llm_agent/test_events.py
git commit -m "feat(llm-agent): 도구 호출 자문 이벤트"
```

---

## Task 9: `LlmExecutor` — A2A 경계

**Files:**
- Create: `packages/llm_agent/executor.py`
- Test: `tests/llm_agent/test_executor.py`

**Interfaces:**
- Consumes: `run_loop`, `LoopFailed`, `ROLES`, `ToolBridge`, `tool_schemas`, `OllamaClient`, `record_tool_event`
- Produces:
  - `class LlmExecutor(AgentExecutor)` — `execute`/`cancel`
  - `def payload_to_part(payload: dict) -> Part` (SP1 `stub_agent.executor._payload_to_part` 와 동일 동작)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/llm_agent/test_executor.py
import pytest

from llm_agent.executor import LlmExecutor


class _Queue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


class _Ctx:
    def __init__(self, payload: dict) -> None:
        self.task_id = "t-1"
        self.context_id = "c-1"
        self.current_task = None
        self._payload = payload

    def get_user_input(self):  # 사용하지 않는다
        return ""


async def test_dispatch_payload_fields_reach_the_loop(monkeypatch) -> None:
    """오케스트레이터가 넓힌 페이로드를 그대로 쓴다 (스펙 §4.3)."""
    seen = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)
        from llm_agent.loop import LoopResult
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                     mcp_url="http://y/mcp", session_maker=None)
    await ex.run_task({"requirement_id": "REQ-1", "title": "계산기",
                       "revision": 2, "feedback": [{"agent": "qa", "verdict": "FAIL",
                                                    "summary": "틀렸다"}]})
    assert seen["title"] == "계산기"
    assert seen["revision"] == 2
    assert seen["feedback"][0]["summary"] == "틀렸다"


async def test_missing_title_is_not_fatal(monkeypatch) -> None:
    """SP1 형식 페이로드(requirement_id 만)로도 죽지 않는다."""
    async def fake_loop(**kwargs):
        from llm_agent.loop import LoopResult
        assert kwargs["title"] == "REQ-1"
        assert kwargs["revision"] == 1
        assert kwargs["feedback"] == []
        return LoopResult(payload={"kind": "source_code", "agent": "dev", "exit_code": 0}, turns=1)

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                     mcp_url="http://y/mcp", session_maker=None)
    await ex.run_task({"requirement_id": "REQ-1"})


async def test_loop_failure_propagates_so_sdk_marks_task_errored(monkeypatch) -> None:
    """LoopFailed 를 삼키지 않는다 — SDK 가 TASK_STATE_ERROR 로 바꾼다."""
    from llm_agent.loop import LoopFailed

    async def fake_loop(**kwargs):
        raise LoopFailed("턴 상한")

    monkeypatch.setattr("llm_agent.executor.run_loop", fake_loop)
    ex = LlmExecutor(agent="dev", ollama_url="http://x", model="qwen3:8b",
                     mcp_url="http://y/mcp", session_maker=None)
    with pytest.raises(LoopFailed):
        await ex.run_task({"requirement_id": "REQ-1"})


async def test_unknown_agent_is_a_configuration_error() -> None:
    with pytest.raises(KeyError):
        LlmExecutor(agent="wat", ollama_url="http://x", model="qwen3:8b",
                    mcp_url="http://y/mcp", session_maker=None)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/llm_agent/test_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_agent.executor'`

- [ ] **Step 3: 구현한다**

```python
# packages/llm_agent/executor.py
"""A2A `AgentExecutor` 구현. SP1 의 `StubExecutor` 자리에 들어간다.

SP1 의 이벤트 순서 규칙을 그대로 지킨다 — **Task 이벤트를 상태 갱신보다 먼저**
큐에 넣어야 한다. SDK 의 `TaskManager` 는 Task 가 저장되기 전에 도착한
`TaskStatusUpdateEvent` 를 `InvalidAgentResponseError` 로 거절한다.

`LoopFailed` 는 잡지 않고 전파한다. SDK 가 처리되지 않은 예외를
`TASK_STATE_ERROR` 로 바꾸고, 오케스트레이터의 재시도·환류 상한이 이어받는다
(스펙 §10).
"""
from __future__ import annotations

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState
from a2a.utils import new_task
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from llm_agent.events import record_tool_event
from llm_agent.loop import run_loop
from llm_agent.mcp_client import ToolBridge, tool_schemas
from llm_agent.ollama import OllamaClient
from llm_agent.roles import ROLES


def payload_to_part(payload: dict) -> Part:
    value = struct_pb2.Value()
    ParseDict(payload, value)
    return Part(data=value)


class LlmExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        agent: str,
        ollama_url: str,
        model: str,
        mcp_url: str,
        session_maker,
    ) -> None:
        self.role = ROLES[agent]  # 알 수 없는 역할은 설정 오류다 — 여기서 터진다
        self._ollama_url = ollama_url
        self._model = model
        self._mcp_url = mcp_url
        self._session_maker = session_maker

    async def run_task(self, payload: dict) -> dict:
        """디스패치 페이로드를 받아 산출물 payload 를 돌려준다.

        `execute` 에서 분리해 둔 이유는 A2A 이벤트 배관 없이 단위 테스트할 수
        있게 하기 위해서다.
        """
        requirement_id = payload["requirement_id"]
        title = payload.get("title") or requirement_id
        revision = int(payload.get("revision") or 1)
        feedback = payload.get("feedback") or []

        def on_tool(name: str, args: dict, result: dict) -> None:
            if self._session_maker is None:
                return
            import asyncio

            asyncio.get_running_loop().create_task(
                record_tool_event(
                    self._session_maker, requirement_id=requirement_id,
                    agent=self.role.name, revision=revision, tool=name, result=result,
                )
            )

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as http:
            llm = OllamaClient(self._ollama_url, self._model, http)
            async with streamablehttp_client(self._mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    raw = [
                        {"name": t.name, "description": t.description or "",
                         "inputSchema": t.inputSchema or {}}
                        for t in listed.tools
                    ]
                    schemas = tool_schemas(self.role.tools, raw)
                    bridge = ToolBridge(session, requirement_id, self.role.tools)
                    result = await run_loop(
                        role=self.role, llm=llm, bridge=bridge, schemas=schemas,
                        title=title, revision=revision, feedback=feedback,
                        on_tool=on_tool,
                    )
        return result.payload

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    state=TaskState.TASK_STATE_SUBMITTED,
                )
            )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        payload = await self.run_task(_incoming_payload(context))
        await updater.add_artifact([payload_to_part(payload)], name=payload["kind"])
        if payload["exit_code"] == 0:
            await updater.complete()
        else:
            await updater.failed()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.failed()


def _incoming_payload(context: RequestContext) -> dict:
    """A2A 메시지의 data Part 에서 디스패치 페이로드를 꺼낸다."""
    from google.protobuf.json_format import MessageToDict

    for part in context.message.parts:
        if part.HasField("data"):
            return MessageToDict(part.data)
    return {}
```

> **구현자 주의.** `_incoming_payload` 의 실제 형태는 SDK 1.1.2 의 `RequestContext.message`
> 구조에 달렸다. `tests/integration/` 에서 SP1 의 스텁이 받는 메시지를 로깅해 실제 구조를
> 확인한 뒤 맞춰라. 이 함수가 빈 dict 를 돌려주면 `payload["requirement_id"]` 에서
> `KeyError` 가 나고 그것은 POISON 으로 분류된다 — 조용히 실패하지 않는다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/llm_agent/test_executor.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add packages/llm_agent/executor.py tests/llm_agent/test_executor.py
git commit -m "feat(llm-agent): A2A Executor 구현"
```

---

## Task 10: 디스패치 페이로드 확장

**Files:**
- Modify: `packages/orchestrator/engine.py`
- Test: `tests/orchestrator/test_dispatch_payload.py`

**Interfaces:**
- Produces:
  - `async def _dispatch_payload(self, s, req) -> dict` — `{requirement_id, title, revision, feedback}`
  - `feedback` 는 직전 회차 검증자 아티팩트에서 만든다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/orchestrator/test_dispatch_payload.py
from orchestrator.engine import build_feedback


def test_first_revision_has_no_feedback() -> None:
    assert build_feedback([], revision=1) == []


def test_feedback_comes_from_previous_revision_verifier_artifacts() -> None:
    artifacts = [
        {"revision": 1, "agent": "qa", "verdict": "FAIL", "summary": "test_add 실패"},
        {"revision": 1, "agent": "security", "verdict": "PASS", "summary": ""},
    ]
    out = build_feedback(artifacts, revision=2)
    assert {f["agent"] for f in out} == {"qa", "security"}
    assert next(f for f in out if f["agent"] == "qa")["summary"] == "test_add 실패"


def test_only_the_immediately_previous_revision_is_used() -> None:
    artifacts = [
        {"revision": 1, "agent": "qa", "verdict": "FAIL", "summary": "오래된 지적"},
        {"revision": 2, "agent": "qa", "verdict": "FAIL", "summary": "최근 지적"},
    ]
    out = build_feedback(artifacts, revision=3)
    assert [f["summary"] for f in out] == ["최근 지적"]


def test_non_verifier_artifacts_are_ignored() -> None:
    artifacts = [
        {"revision": 1, "agent": "dev", "verdict": None, "summary": "코드 썼다"},
        {"revision": 1, "agent": "qa", "verdict": "FAIL", "summary": "실패"},
    ]
    out = build_feedback(artifacts, revision=2)
    assert [f["agent"] for f in out] == ["qa"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/orchestrator/test_dispatch_payload.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_feedback'`

- [ ] **Step 3: 구현한다**

`packages/orchestrator/engine.py` 상단(클래스 밖)에 추가한다:

```python
#: 검증자 역할. `VERIFIERS` 가 이미 있으면 그것을 쓴다.
def build_feedback(artifacts: list[dict], revision: int) -> list[dict]:
    """직전 회차 검증자 아티팩트로 환류 피드백을 만든다.

    LLM 에이전트는 무엇이 왜 반려됐는지 알아야 고칠 수 있다 (스펙 §4.3).
    완료 기준 4 가 요구하는 "환류가 실제 실패 출력에 근거한다"의 출발점이다.
    """
    if revision <= 1:
        return []
    previous = revision - 1
    return [
        {"agent": a["agent"], "verdict": a["verdict"], "summary": a.get("summary", "")}
        for a in artifacts
        if a.get("revision") == previous and a.get("verdict") is not None
    ]
```

`dispatch_agent` 안의 `client.submit({"requirement_id": requirement_id}, key)` 를 다음으로 바꾼다. 아티팩트 조회는 이미 열린 세션 블록 안에서 하고, 결과를 지역 변수에 담아 `submit` 호출에 쓴다:

```python
                    client.submit(dispatch_payload, key),
```

세션 블록 안(`task_id = task.task_id` 직전)에 추가:

```python
            rows = (
                await s.execute(
                    select(Artifact, WorkflowTask)
                    .join(WorkflowTask, Artifact.producer_task == WorkflowTask.task_id)
                    .where(Artifact.requirement_id == requirement_id)
                )
            ).all()
            artifacts = [
                {
                    "revision": t.revision,
                    "agent": t.agent,
                    "verdict": t.verdict,
                    "summary": a.content.get("summary", ""),
                }
                for a, t in rows
            ]
            dispatch_payload = {
                "requirement_id": requirement_id,
                "title": req.title,
                "revision": req.revision,
                "feedback": build_feedback(artifacts, req.revision),
            }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/orchestrator/test_dispatch_payload.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: SP1 회귀를 확인한다**

Run: `pytest tests/ -q`
Expected: 기존 144개 + 신규 전부 PASS. 스텁은 추가 필드를 무시하므로 영향이 없어야 한다.

- [ ] **Step 6: 커밋**

```bash
git add packages/orchestrator/engine.py tests/orchestrator/test_dispatch_payload.py
git commit -m "feat(orchestrator): 디스패치 페이로드에 제목·회차·환류 피드백을 싣는다"
```

---

## Task 11: `run_s` backstop 배선

**Files:**
- Modify: `packages/orchestrator/reconciler.py`, `packages/orchestrator/policy.py`
- Test: `tests/orchestrator/test_run_budget.py`

**Interfaces:**
- Consumes: `Reconciler.next_action`
- Produces: `next_action` 이 요구사항 나이 > `run_s` 일 때 `GIVE_UP` 을 돌려준다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/orchestrator/test_run_budget.py
from datetime import datetime, timedelta, timezone

from orchestrator.reconciler import Action, next_action


def _req(created_minutes_ago: int):
    class R:
        requirement_id = "REQ-1"
        state = "implementing"
        revision = 1
        max_revisions = 3
        created_at = datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago)
    return R()


def test_young_requirement_is_not_given_up() -> None:
    action = next_action(
        _req(5), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=5.0, stuck_after_s=900.0, run_s=3600.0,
    )
    assert action is not Action.GIVE_UP


def test_requirement_past_run_budget_is_given_up() -> None:
    """스펙 §9.2 — run_s 를 넘기면 더 묻지 않는다."""
    action = next_action(
        _req(61), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=5.0, stuck_after_s=900.0, run_s=3600.0,
    )
    assert action is Action.GIVE_UP


def test_run_budget_wins_over_other_actions() -> None:
    """예산 초과는 다른 어떤 조치보다 우선한다 — 무한 진행을 막는 backstop 이다."""
    action = next_action(
        _req(120), rows=[], now=datetime.now(timezone.utc),
        stale_after_s=5.0, stuck_after_s=900.0, run_s=3600.0,
    )
    assert action is Action.GIVE_UP
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/orchestrator/test_run_budget.py -v`
Expected: FAIL — `next_action() got an unexpected keyword argument 'run_s'`

구현자 주의: `next_action` 의 실제 시그니처는 `reconciler.py` 에서 확인하라. 위 테스트는 `run_s` 를 키워드로 추가하는 형태를 가정한다. 기존 호출부(`Reconciler._execute`)도 함께 고쳐야 한다.

- [ ] **Step 3: 구현한다**

`next_action` 에 `run_s: float` 파라미터를 추가하고, **함수 맨 앞**(다른 어떤 판단보다 먼저)에 다음을 둔다:

```python
    # 요구사항 전체 시간 예산. SP1 은 배선하지 않았다(스펙 SP1 §12.2) —
    # 스텁은 초 단위로 끝나 시간 축 규칙이 필요 없었기 때문이다. SP2 에서
    # 실제 LLM 지연이 붙어 "느린 것"과 "멈춘 것"을 나이만으로 가르기 어려워졌고,
    # 그래서 요구사항에도 상한이 필요해졌다.
    if (now - req.created_at).total_seconds() > run_s:
        return Action.GIVE_UP
```

`Reconciler.__init__` 에 `run_s: float` 를 받아 `_execute` 에서 넘긴다. `policy.py` 의 모듈 docstring 표에서 `run_s` 행을 "**집행됨.** 리컨실러가 요구사항 나이로 검사한다"로 고친다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/orchestrator/test_run_budget.py tests/orchestrator/ -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add packages/orchestrator/reconciler.py packages/orchestrator/policy.py tests/orchestrator/test_run_budget.py
git commit -m "feat(orchestrator): run_s 를 요구사항 나이 backstop 으로 배선한다"
```

---

## Task 12: 에이전트 진입점 모드 분기

**Files:**
- Create: `packages/agent_entry/__init__.py`, `packages/agent_entry/main.py`
- Modify: `docker-compose.yml`
- Test: `tests/agent_entry/test_mode.py`

**Interfaces:**
- Consumes: `StubExecutor`, `LlmExecutor`
- Produces:
  - `def build_executor(mode: str, agent: str, *, scenario_path: str, ollama_url: str, model: str, mcp_url: str, session_maker)` — 모드에 따라 실행기를 고른다
  - `class UnknownMode(ValueError)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/agent_entry/test_mode.py
import pytest

from agent_entry.main import UnknownMode, build_executor
from llm_agent.executor import LlmExecutor
from stub_agent.executor import StubExecutor

KW = dict(scenario_path="scenarios/all_pass.yaml", ollama_url="http://x",
          model="qwen3:8b", mcp_url="http://y/mcp", session_maker=None)


def test_stub_mode_keeps_sp1_behaviour() -> None:
    assert isinstance(build_executor("stub", "dev", **KW), StubExecutor)


def test_llm_mode_builds_llm_executor() -> None:
    assert isinstance(build_executor("llm", "dev", **KW), LlmExecutor)


def test_unknown_mode_is_rejected_loudly() -> None:
    with pytest.raises(UnknownMode):
        build_executor("magic", "dev", **KW)


def test_default_mode_is_stub() -> None:
    """기본값이 stub 인 것은 의도다 — SP1 의 151개 테스트가 그대로 돌아야 한다."""
    from agent_entry.main import DEFAULT_MODE

    assert DEFAULT_MODE == "stub"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/agent_entry/test_mode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_entry'`

- [ ] **Step 3: 구현한다**

```python
# packages/agent_entry/main.py
"""에이전트 진입점. 모드에 따라 실행기를 고른다.

기본값이 `stub` 인 것은 의도다 — SP1 의 151개 테스트가 그대로 돌아야 하고
(스펙 §12.1), 새 기본값이 조용히 LLM 을 부르는 일이 없어야 한다.
"""
from __future__ import annotations

import os

from a2a.server.tasks import DatabaseTaskStore

from agent_runtime.app import create_agent_app
from agent_runtime.card import build_card
from agent_runtime.telemetry import setup_tracing
from llm_agent.executor import LlmExecutor
from llm_agent.roles import ROLES
from orchestrator.db import make_engine
from stub_agent.executor import ARTIFACT_KIND, StubExecutor
from stub_agent.scenario import AgentScenario

DEFAULT_MODE = "stub"


class UnknownMode(ValueError):
    """`VSI_AGENT_MODE` 가 stub 도 llm 도 아니다."""


def build_executor(
    mode: str,
    agent: str,
    *,
    scenario_path: str,
    ollama_url: str,
    model: str,
    mcp_url: str,
    session_maker,
):
    if mode == "stub":
        return StubExecutor(AgentScenario.from_yaml(scenario_path, agent), agent)
    if mode == "llm":
        return LlmExecutor(
            agent=agent, ollama_url=ollama_url, model=model,
            mcp_url=mcp_url, session_maker=session_maker,
        )
    raise UnknownMode(f"{mode!r} — stub 또는 llm 이어야 한다")


AGENT = os.environ["VSI_AGENT"]
MODE = os.environ.get("VSI_AGENT_MODE", DEFAULT_MODE)
BASE_URL = os.environ["VSI_BASE_URL"]
DB_URL = os.environ["VSI_DATABASE_URL"]

setup_tracing(AGENT, endpoint=os.environ.get("VSI_OTLP_ENDPOINT"))

engine = make_engine(DB_URL)
card = build_card(
    name=AGENT,
    description=f"{AGENT} 에이전트 ({MODE})",
    skills=[ROLES[AGENT].artifact_kind if MODE == "llm" else ARTIFACT_KIND[AGENT]],
    base_url=BASE_URL,
)
task_store = DatabaseTaskStore(engine=engine, create_table=False, table_name="tasks")

from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

executor = build_executor(
    MODE,
    AGENT,
    scenario_path=os.environ.get("VSI_SCENARIO", "scenarios/all_pass.yaml"),
    ollama_url=os.environ.get("VSI_OLLAMA_URL", "http://host.docker.internal:11434"),
    model=os.environ.get("VSI_MODEL", "qwen3:8b"),
    mcp_url=os.environ.get("VSI_MCP_URL", "http://workspace:8000/mcp"),
    session_maker=async_sessionmaker(engine, expire_on_commit=False),
)
app = create_agent_app(card, executor, task_store)
```

`docker-compose.yml` 의 에이전트 4종에서 `uvicorn stub_agent.main:app` 을 `agent_entry.main:app` 으로 바꾸고, 환경변수 `VSI_AGENT_MODE: ${VSI_AGENT_MODE:-stub}` 를 추가한다. `Dockerfile` 의 `CMD` 도 함께 바꾼다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/agent_entry/test_mode.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: SP1 회귀를 확인한다**

Run: `docker compose up -d --build && pytest tests/ -q`
Expected: 기존 144개 전부 PASS (스텁 모드 기본값)

- [ ] **Step 6: 커밋**

```bash
git add packages/agent_entry/ tests/agent_entry/ docker-compose.yml Dockerfile
git commit -m "feat(agent): VSI_AGENT_MODE 로 스텁과 LLM 실행기를 가른다"
```

---

## Task 13: `workspace` 서비스와 네트워크 격리

**Files:**
- Modify: `docker-compose.yml`
- Test: `tests/integration/test_isolation.py`

**Interfaces:**
- Produces: compose 에 `workspace` 서비스, `internal` 네트워크

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/integration/test_isolation.py
from pathlib import Path

import yaml

from tests.integration.harness import REPO_ROOT


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


def test_workspace_service_exists() -> None:
    assert "workspace" in _compose()["services"]


def test_workspace_has_no_internet_route() -> None:
    """생성 코드가 외부로 나갈 경로가 네트워크 수준에 없어야 한다 (스펙 §4.2)."""
    compose = _compose()
    nets = compose["services"]["workspace"]["networks"]
    assert list(nets) == ["internal"]
    assert compose["networks"]["internal"]["internal"] is True


def test_workspace_mounts_no_host_path() -> None:
    assert "volumes" not in _compose()["services"]["workspace"]


def test_agents_reach_both_networks() -> None:
    """에이전트는 MCP(internal)와 Ollama(기본)를 모두 써야 한다."""
    services = _compose()["services"]
    for agent in ("planner", "dev", "qa", "security"):
        nets = set(services[agent].get("networks") or [])
        assert {"internal", "default"} <= nets, agent


def test_orchestrator_is_not_on_internal_network() -> None:
    """오케스트레이터는 워크스페이스에 닿을 이유가 없다."""
    nets = _compose()["services"]["orchestrator"].get("networks") or ["default"]
    assert "internal" not in nets
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/integration/test_isolation.py -v`
Expected: FAIL — `KeyError: 'workspace'`

- [ ] **Step 3: 구현한다**

`docker-compose.yml` 에 추가한다:

```yaml
  workspace:
    build: .
    command: ["uvicorn", "tool_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      VSI_WORKSPACE_BASE: /workspace
    # 볼륨을 붙이지 않는다 — 생성 코드는 컨테이너 안에서만 산다.
    # 불변 기록은 DB 의 artifacts 테이블이 담당한다(스펙 §6.3).
    networks: [internal]

networks:
  default:
  internal:
    internal: true
```

에이전트 4종에 `networks: [internal, default]` 를 추가한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/integration/test_isolation.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 기동을 확인한다**

```bash
docker compose up -d --build workspace
docker compose exec -T workspace python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/mcp', timeout=5).status)"
```

Expected: HTTP 응답(405 또는 200). 연결 자체가 되면 성공이다.

- [ ] **Step 6: 커밋**

```bash
git add docker-compose.yml tests/integration/test_isolation.py
git commit -m "feat(compose): 인터넷 없는 workspace 컨테이너와 internal 네트워크"
```

---

## Task 14: 상한 값 조정

**Files:**
- Modify: `packages/orchestrator/reconciler.py`, `docker-compose.yml`
- Test: `tests/orchestrator/test_caps.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/orchestrator/test_caps.py
from orchestrator.reconciler import DEFAULT_STUCK_AFTER_S


def test_stuck_ceiling_clears_the_agent_budget() -> None:
    """에이전트 예산(600초)보다 커야 정상 작업이 학살당하지 않는다 (스펙 §9.2)."""
    from llm_agent.loop import BUDGET_S

    assert DEFAULT_STUCK_AFTER_S > BUDGET_S
    assert DEFAULT_STUCK_AFTER_S == 900.0


def test_turn_cap_and_budget_match_the_spec() -> None:
    from llm_agent.loop import BUDGET_S, MAX_TURNS

    assert MAX_TURNS == 12
    assert BUDGET_S == 600.0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/orchestrator/test_caps.py -v`
Expected: FAIL — `assert 60.0 == 900.0`

- [ ] **Step 3: 구현한다**

`reconciler.py` 의 `DEFAULT_STUCK_AFTER_S` 를 `900.0` 으로 바꾸고 주석을 갱신한다:

```python
#: 열린 행의 나이 천장. SP1 은 60초였다 — 스텁이 초 단위로 끝났기 때문이다.
#: SP2 의 LLM 작업은 몇 분이 기본이라 60초를 두면 **정상 작업이 전부 강제
#: 실패당한다**(SP1 스펙 §12.3 이 경고한 그대로다). 에이전트가 자기 예산
#: 600초를 스스로 집행하므로(스펙 §9.2), 이 천장은 그보다 큰 값으로 두어
#: "느린 작업"이 아니라 "죽은 프로세스"만 잡게 한다.
DEFAULT_STUCK_AFTER_S = 900.0
```

`docker-compose.yml` 의 오케스트레이터에 `VSI_RECONCILE_STUCK_S: ${VSI_RECONCILE_STUCK_S:-900}` 을 둔다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/orchestrator/test_caps.py tests/ -q`
Expected: PASS. SP1 통합 테스트가 900초 천장에서도 통과해야 한다(스텁은 초 단위로 끝나므로 영향 없음).

- [ ] **Step 5: 커밋**

```bash
git add packages/orchestrator/reconciler.py docker-compose.yml tests/orchestrator/test_caps.py
git commit -m "feat(reconciler): 정체 천장을 900초로 올린다"
```

---

## Task 15: UI 에 도구 활동 표시

**Files:**
- Modify: `web/src/useEventStream.ts`, `web/src/AgentGraph.tsx`
- Test: `web/src/useEventStream.test.ts`

**Interfaces:**
- Produces: `StreamState.activity: Record<string, string>` — 에이전트별 현재 동작 문구

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`web/src/useEventStream.test.ts` 에 추가한다:

```typescript
it("tool_result 를 받으면 에이전트의 현재 동작을 기록한다", () => {
  const state = reduceEvents(initialStreamState, [
    { event_id: 1, event_type: "tool_result", aggregate_id: "REQ-1",
      payload: { agent: "dev", tool: "write_file", revision: 1, ok: true, detail: "calc.py 에 썼다" } },
  ]);
  expect(state.activity.dev).toContain("write_file");
});

it("도구 이벤트는 워크플로 상태를 바꾸지 않는다", () => {
  // 자문 이벤트다 — 상태를 유도하면 안 된다 (스펙 §8.1)
  const before = reduceEvents(initialStreamState, [
    { event_id: 1, event_type: "state_changed", aggregate_id: "REQ-1",
      payload: { to: "implementing", signal: "plan_ready" } },
  ]);
  const after = reduceEvents(before, [
    { event_id: 2, event_type: "tool_result", aggregate_id: "REQ-1",
      payload: { agent: "qa", tool: "run_tests", revision: 1, ok: false, exit_code: 1, detail: "실패" } },
  ]);
  expect(after.state).toBe(before.state);
  expect(after.lastFailedVerifier).toBe(before.lastFailedVerifier);
});

it("이미 본 도구 이벤트는 중복 반영하지 않는다", () => {
  const evt = { event_id: 7, event_type: "tool_result", aggregate_id: "REQ-1",
                payload: { agent: "dev", tool: "read_file", revision: 1, ok: true, detail: "" } };
  const once = reduceEvents(initialStreamState, [evt]);
  const twice = reduceEvents(once, [evt]);
  expect(twice.activity).toEqual(once.activity);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd web && npx vitest run`
Expected: FAIL — `activity` 가 undefined

- [ ] **Step 3: 구현한다**

`useEventStream.ts` 의 `StreamState` 에 `activity: Record<string, string>` 를 추가하고 `initialStreamState` 에 `activity: {}` 를 넣는다. `reduceEvents` 의 이벤트 분기에 추가한다:

```typescript
    if (event.event_type === "tool_result") {
      // 자문 이벤트다 — activity 외의 어떤 필드도 건드리지 않는다 (스펙 §8.1).
      const agent = String(event.payload.agent ?? "");
      const tool = String(event.payload.tool ?? "");
      if (agent && tool) {
        next.activity = { ...next.activity, [agent]: tool };
      }
      continue;
    }
```

`AgentGraph.tsx` 에서 각 에이전트 노드 아래에 `activity[agent]` 를 작은 글씨로 표시한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd web && npx vitest run`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```bash
git add web/src/useEventStream.ts web/src/AgentGraph.tsx web/src/useEventStream.test.ts
git commit -m "feat(web): 에이전트 노드에 현재 도구 활동을 표시한다"
```

---

## Task 16: 실제 모델 통합 테스트

**Files:**
- Create: `tests/integration/test_llm_end_to_end.py`
- Modify: `pyproject.toml` (pytest 마커 등록)
- Test: 자기 자신

**Interfaces:**
- Consumes: `tests/integration/harness.py` 의 `run_scenario`·`collect`·`db_session`

- [ ] **Step 1: 마커를 등록한다**

`pyproject.toml` 의 `[tool.pytest.ini_options]` 에 추가:

```toml
markers = [
    "llm: 실제 로컬 모델을 부른다. 느리고 비결정적이다. CI 기본 실행에서 제외한다.",
]
addopts = "-m 'not llm'"
```

- [ ] **Step 2: 테스트를 쓴다**

```python
# tests/integration/test_llm_end_to_end.py
"""실제 모델로 도는 소수의 테스트.

**모델 출력 내용은 절대 단언하지 않는다** (스펙 §12.4). 단언하는 것은 성질이다 —
종단 상태에 도달했는가, 판정이 종료코드와 일치하는가, 환류가 실제 실패 출력을
실어 날랐는가.

실행 전 준비: `ollama serve` 가 떠 있어야 하고 `qwen3:8b` 가 받아져 있어야 한다.
`VSI_AGENT_MODE=llm docker compose up -d --force-recreate planner dev qa security`
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from orchestrator.models import OutboxEvent, WorkflowTask
from orchestrator.workflow import RequirementState
from tests.integration.harness import collect, db_session, run_scenario

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

TERMINAL = {RequirementState.ACCEPTED, RequirementState.ESCALATED}


async def test_requirement_reaches_a_terminal_state_without_intervention() -> None:
    """완료 기준 1 — accepted 와 escalated 모두 성공이다 (스펙 §13)."""
    result = await run_scenario(None, "REQ-LLM-1", "정수 두 개를 더하는 add(a, b) 함수")
    assert result.state in TERMINAL


async def test_verifier_verdicts_match_their_tool_exit_codes() -> None:
    """완료 기준 3 — LLM 이 판정을 뒤집지 못한다 (스펙 §13)."""
    await run_scenario(None, "REQ-LLM-2", "정수 두 개를 더하는 add(a, b) 함수")
    async with db_session() as s:
        tasks = (
            await s.execute(
                select(WorkflowTask).where(
                    WorkflowTask.requirement_id == "REQ-LLM-2",
                    WorkflowTask.agent.in_(("qa", "security")),
                    WorkflowTask.state == "completed",
                )
            )
        ).scalars().all()
        events = (
            await s.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == "REQ-LLM-2",
                    OutboxEvent.event_type == "tool_result",
                )
            )
        ).scalars().all()

    assert tasks, "검증자 작업이 하나도 완료되지 않았다"
    exit_codes = {
        (e.payload["agent"], e.payload["revision"]): e.payload.get("exit_code")
        for e in events
        if e.payload.get("exit_code") is not None
    }
    for task in tasks:
        code = exit_codes.get((task.agent, task.revision))
        if code is None:
            continue
        assert task.verdict == ("PASS" if code == 0 else "FAIL"), (
            f"{task.agent} rev{task.revision}: verdict={task.verdict} 인데 exit_code={code}"
        )


async def test_agents_actually_called_tools() -> None:
    """도구를 부르지 않은 산출물은 근거가 없다 (스펙 §5.3)."""
    await run_scenario(None, "REQ-LLM-3", "정수 두 개를 더하는 add(a, b) 함수")
    async with db_session() as s:
        events = (
            await s.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == "REQ-LLM-3",
                    OutboxEvent.event_type == "tool_result",
                )
            )
        ).scalars().all()
    assert events, "도구 호출 이벤트가 하나도 없다"
    assert {e.payload["agent"] for e in events} >= {"planner", "dev"}
```

- [ ] **Step 3: 스텁 모드에서 제외되는지 확인한다**

Run: `pytest tests/ -q`
Expected: `test_llm_end_to_end.py` 의 테스트가 **수집되지 않는다**(`-m 'not llm'`). 기존 개수만 통과.

- [ ] **Step 4: 실제 모델로 돌려본다**

```bash
ollama serve &
ollama pull qwen3:8b
VSI_AGENT_MODE=llm docker compose up -d --build
pytest tests/integration/test_llm_end_to_end.py -m llm -v
```

Expected: 3개 PASS. 오래 걸린다(요구사항당 5~10분).

- [ ] **Step 5: 커밋**

```bash
git add tests/integration/test_llm_end_to_end.py pyproject.toml
git commit -m "test(llm): 실제 모델 통합 테스트 — 성질만 단언한다"
```

---

## Task 17: 비용 0원 스캔 확장과 문서

**Files:**
- Modify: `tests/integration/test_acceptance.py`, `README.md`
- Test: 자기 자신

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/integration/test_acceptance.py` 에 추가한다:

```python
def test_no_external_llm_api_anywhere_including_llm_agent() -> None:
    """완료 기준 6 — 외부 LLM API 호출 0회 (스펙 §13).

    SP1 의 스캔은 packages/·services/ 를 훑었다. SP2 가 추가한 llm_agent 도
    같은 규칙을 받는다.
    """
    forbidden = ("api.anthropic.com", "api.openai.com", "openai", "anthropic")
    scanned = []
    for path in (REPO_ROOT / "packages").rglob("*.py"):
        scanned.append(path)
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path}: {token}"
    assert scanned, "스캔한 파일이 없다 — 경로가 틀렸다"


def test_ollama_is_the_only_llm_endpoint() -> None:
    text = (REPO_ROOT / "packages" / "llm_agent" / "ollama.py").read_text(encoding="utf-8")
    assert "/api/chat" in text
    assert "https://" not in text
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/integration/test_acceptance.py -v`
Expected: FAIL 또는 PASS. FAIL 이면 위반을 고친다.

- [ ] **Step 3: README 를 갱신한다**

`README.md` 의 상태 표에서 SP2 행을 `**구현 완료** · 완료 정의 6개 테스트로 고정` 으로 바꾸고, 실행 절에 다음을 추가한다:

```markdown
## LLM 모드로 실행

로컬 Ollama 가 필요하다. 비용은 0원이다.

```bash
ollama serve &
ollama pull qwen3:8b
VSI_AGENT_MODE=llm docker compose up -d --build
curl -X POST http://localhost:8000/requirements \
  -H 'Content-Type: application/json' \
  -d '{"requirement_id":"REQ-1","title":"정수 두 개를 더하는 add(a, b) 함수","run_id":"run-1"}'
```

요구사항 하나가 5~10분 걸린다. `http://localhost:5173` 에서 에이전트가 어떤 도구를
쓰는지 실시간으로 보인다.

스텁 모드(기본값)는 `VSI_AGENT_MODE` 없이 그대로 쓴다 — 1초 만에 끝나고 LLM 을
부르지 않는다.
```

- [ ] **Step 4: 전체 스위트를 돌린다**

Run: `pytest tests/ -q && cd web && npx vitest run`
Expected: 전부 PASS. SP1 의 144개가 그대로 살아 있어야 한다.

- [ ] **Step 5: 커밋**

```bash
git add tests/integration/test_acceptance.py README.md
git commit -m "test(acceptance): 비용 0원 스캔을 llm_agent 까지 넓힌다"
```

---

## 자체 검토

**스펙 커버리지**

| 스펙 절 | 태스크 |
|---|---|
| §4.1 교체 지점 | 9, 12 |
| §4.2 네트워크 격리 | 13 |
| §4.3 디스패치 페이로드 | 10 |
| §5.1 루프 | 7 |
| §5.2 역할별 권한 | 3, 6 |
| §5.3 판정은 종료코드에서 | 7 (테스트 2건), 16 |
| §5.4 개발은 테스트를 못 돌린다 | 3 (테스트), 6 |
| §6.1 경로 봉쇄 | 1, 5 |
| §6.2 도구 명세·절단 | 2 |
| §6.3 파일시스템과 DB | 13 (볼륨 없음) |
| §7 기획이 인수 테스트를 쓴다 | 6 |
| §8.1 자문 이벤트 | 8, 15 |
| §9.2 상한 층 | 7, 11, 14 |
| §10 실패 분류 | 4 (503), 9 (전파) |
| §11.1 UI | 15 |
| §12.1~12.4 테스트 4층 | 12(1층), 1~3(2층), 4~9(3층), 16(4층) |
| §13 완료 정의 | 16, 17 |

**미반영 항목:** §11.2 트레이스 속성(`vsi.llm.*`, `vsi.tool.*`)은 별도 태스크를 두지 않았다. SP1 의 `setup_tracing` 이 이미 자동 계측을 걸고 있어 HTTP 스팬은 나오지만, 명시적 속성은 붙지 않는다. **관측성 강화는 SP2 완료 후 별도 작업으로 다룬다** — 완료 정의 6개 중 어느 것도 이 속성에 의존하지 않는다.

**타입 일관성**

- `ToolResult(ok, detail, exit_code)` — Task 2 정의, Task 3 에서 dict 로 직렬화, Task 7·8 에서 dict 로 소비. 키 이름 일치 확인.
- `payload` 의 `kind`·`agent`·`exit_code`·`verdict`·`summary` — SP1 `stub_agent.executor.build_payload` 와 동일 키. Task 7 이 `summary` 를 추가하는데 SP1 스텁에는 없다. 오케스트레이터는 `summary` 를 읽지 않으므로 무해하고, Task 10 의 `build_feedback` 이 아티팩트 `content` 에서 읽는다.
- `Role.tools` (Task 6) 와 `TOOLS_BY_ROLE` (Task 3) 이 같은 값을 두 곳에 둔다. **의도적 중복이다** — 서버는 에이전트를 신뢰하지 않고 자기 권한 표를 갖는다. Task 3 과 Task 6 의 테스트가 각각 스펙 §5.2 를 고정하므로 어긋나면 둘 다 깨진다.
