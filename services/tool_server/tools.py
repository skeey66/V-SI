"""MCP 가 노출하는 도구 5종.

모든 함수가 예외 대신 `ToolResult` 를 돌려준다. 도구 오류는 모델이 보고 고쳐야
하는 정보이지 프로세스를 죽일 사건이 아니다 (스펙 §6.1).

출력 절단은 보안이 아니라 모델 컨텍스트 보호다 (스펙 §6.2). 8b 모델의 컨텍스트에
pytest 전체 출력을 넣으면 지시가 묻힌다. 꼬리를 남기는 이유는 실패 요약이 끝에
있기 때문이다.
"""
from __future__ import annotations

import subprocess
import sys
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


def _os_error_detail(root: Path, exc: OSError) -> str:
    """OSError 메시지에서 워크스페이스 루트의 절대경로를 지운다.

    errno 범주(예: "Permission denied")는 모델에게 유용하니 남기지만, 절대경로가
    그대로 섞여 나가면 list_files 가 지키는 "루트를 드러내지 않는다" 불변식이
    에러 경로로 새는 우회로가 된다. 루트를 몰라도 resolve_within 이 구조적으로
    탈출을 막으므로 이건 구멍을 막는 게 아니라 일관성을 맞추는 것이다.
    """
    text = str(exc)
    for candidate in {str(root), str(root.resolve())}:
        if not candidate:
            continue
        text = text.replace(candidate + "/", "").replace(candidate, ".")
    return text


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
    try:
        raw = target.read_bytes()
    except PermissionError as exc:
        return ToolResult(
            ok=False, detail=f"{path} 을 읽을 권한이 없다: {_os_error_detail(root, exc)}"
        )
    except OSError as exc:
        return ToolResult(
            ok=False, detail=f"{path} 을 읽는 중 오류가 났다: {_os_error_detail(root, exc)}"
        )
    if len(raw) > READ_LIMIT_BYTES:
        body = raw[:READ_LIMIT_BYTES].decode("utf-8", errors="replace")
        return ToolResult(ok=True, detail=body + "\n...(뒷부분 절단)...")
    return ToolResult(ok=True, detail=raw.decode("utf-8", errors="replace"))


def write_file(root: Path, path: str, content: str) -> ToolResult:
    try:
        target = resolve_within(root, path)
    except PathEscape as exc:
        return ToolResult(ok=False, detail=str(exc))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except IsADirectoryError as exc:
        return ToolResult(
            ok=False,
            detail=f"{path} 은 이미 디렉터리라서 파일로 쓸 수 없다: {_os_error_detail(root, exc)}",
        )
    except (FileExistsError, NotADirectoryError) as exc:
        return ToolResult(
            ok=False,
            detail=(
                f"{path} 의 상위 경로에 이미 파일이 있어 디렉터리를 만들 수 없다: "
                f"{_os_error_detail(root, exc)}"
            ),
        )
    except UnicodeEncodeError as exc:
        return ToolResult(
            ok=False,
            detail=(
                f"{path} 에 쓸 내용에 UTF-8 로 인코딩할 수 없는 문자가 있다"
                f"(깨진 대리쌍 등): {exc}"
            ),
        )
    except OSError as exc:
        return ToolResult(
            ok=False, detail=f"{path} 에 쓰는 중 오류가 났다: {_os_error_detail(root, exc)}"
        )
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
    except OSError as exc:
        return ToolResult(
            ok=False,
            detail=(
                "실행하지 못했다 (작업 디렉터리나 인터프리터를 확인하라): "
                f"{_os_error_detail(root, exc)}"
            ),
        )
    detail = _tail(proc.stdout) + ("\n--- stderr ---\n" + _tail(proc.stderr) if proc.stderr else "")
    return ToolResult(ok=proc.returncode == 0, detail=detail, exit_code=proc.returncode)


def run_tests(root: Path) -> ToolResult:
    return _run(root, [sys.executable, "-m", "pytest", "-q", "--no-header"])


def run_security_scan(root: Path) -> ToolResult:
    return _run(root, [sys.executable, "-m", "bandit", "-r", ".", "-q"])
