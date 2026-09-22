"""MCP 가 노출하는 도구 7종.

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
from pathlib import Path, PurePosixPath

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


def _strip_root(root: Path, text: str) -> str:
    """텍스트에서 워크스페이스 루트의 절대경로를 지운다.

    두 형태(`root`, `root.resolve()`) 를 모두 지운다 — macOS 는 `/var` 가
    `/private/var` 심볼릭 링크라서 base 가 심볼릭 링크 아래 있으면 둘이
    달라진다. 절대경로가 그대로 섞여 나가면 list_files 가 지키는 "루트를
    드러내지 않는다" 불변식이 에러/출력 경로로 새는 우회로가 된다. 루트를
    몰라도 resolve_within 이 구조적으로 탈출을 막으므로 이건 구멍을 막는 게
    아니라 일관성을 맞추는 것이다.

    `root/` 접두사는 통째로 지워 `root/foo.py` 를 `foo.py` 로 만든다 — 이는
    모델이 write_file 에 넘긴 상대경로와 같은 모양이라 오해를 주지 않는다.
    접두사가 아니라 통째로 root 와 일치하는 경우만 "." 로 남긴다.

    두 후보 중 하나가 다른 하나의 부분 문자열일 수 있다 — macOS 에서 `/var` 가
    `/private/var` 심볼릭 링크이므로 `str(root)` 가 `str(root.resolve())` 안에
    그대로 박혀 있는 게 정상이다. `set` 반복 순서는 PYTHONHASHSEED 에 따라
    프로세스마다 달라지는데, 짧은 후보가 먼저 치환되면 긴 후보 속에 파묻힌
    자신의 occurrence 를 갉아먹어 "/private" 같은 접두사 잔해만 남기고 뒤에
    오는 내용에 들러붙는다 (예: `File "/privatetest_calc.py"`). 길이 내림차순
    으로 정렬해 항상 더 길고 구체적인 후보부터 통째로 지우면, 그 안에 파묻힌
    짧은 후보는 이미 사라진 뒤라 부분 치환이 일어날 수 없다 — 순서와 무관하게
    안전하다.
    """
    candidates = sorted({str(root), str(root.resolve())}, key=len, reverse=True)
    for candidate in candidates:
        if not candidate:
            continue
        text = text.replace(candidate + "/", "").replace(candidate, ".")
    return text


def _os_error_detail(root: Path, exc: OSError) -> str:
    """OSError 메시지에서 워크스페이스 루트의 절대경로를 지운다.

    errno 범주(예: "Permission denied")는 모델에게 유용하니 남긴다.
    """
    return _strip_root(root, str(exc))


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


#: 인수 테스트로 보는 경로. 개발 에이전트는 여기에 쓸 수 없다.
#:
#: 인수 테스트는 **개발이 받는 목표**다. 목표를 피검증자가 고쳐 쓸 수 있으면
#: QA 의 판정이 판정이 아니게 된다 — 반려당한 개발이 구현 대신 검사 항목을
#: 고쳐서 통과시킬 수 있기 때문이다.
#:
#: 실측 근거(REQ-SITE-101435, 실제 LLM 실행): 개발이 1회차에 기획이 쓴
#: `test_booking.py` 를 통째로 덮어썼고, QA 는 그 덮어쓴 테스트로 판정했다.
#:
#: 판별은 `resolve_within` 이 정규화한 **상대 경로**에 대고 한다 —
#: `./sub/../test_x.py` 같은 우회를 문자열 검사로 막으려 하지 않는다.
def is_acceptance_test_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if any(p == "tests" for p in parts):
        return True
    name = parts[-1] if parts else ""
    if name == "conftest.py":
        return True
    return (name.startswith("test_") or name.endswith("_test.py")) and name.endswith(".py")


def write_file(
    root: Path, path: str, content: str, *, forbid_tests: bool = False
) -> ToolResult:
    try:
        target = resolve_within(root, path)
    except PathEscape as exc:
        return ToolResult(ok=False, detail=str(exc))
    if forbid_tests and is_acceptance_test_path(target.relative_to(root).as_posix()):
        return ToolResult(
            ok=False,
            detail=(
                f"{path} 은 인수 테스트다 — 이 역할은 테스트 파일을 고칠 수 없다. "
                "테스트는 네가 통과시켜야 할 목표이지 고쳐 쓸 대상이 아니다. "
                "구현 파일을 고쳐라."
            ),
        )
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
    stdout = _strip_root(root, proc.stdout)
    stderr = _strip_root(root, proc.stderr)
    detail = _tail(stdout) + ("\n--- stderr ---\n" + _tail(stderr) if stderr else "")
    return ToolResult(ok=proc.returncode == 0, detail=detail, exit_code=proc.returncode)


def run_tests(root: Path) -> ToolResult:
    return _run(root, [sys.executable, "-m", "pytest", "-q", "--no-header"])


#: 보안 검사에서 제외할 경로. 기획 에이전트가 쓰는 인수 테스트는 `assert` 로
#: 단언하는데, bandit 은 그것을 `B101: assert_used` 로 잡는다(최적화 바이트코드
#: 에서 제거된다는 이유). 즉 **설계대로 동작하는 시스템이 영원히 FAIL 한다** —
#: 실측으로 확인했다: 실제 LLM 실행 3회차 전부 보안이 FAIL 했고 사유가 전부
#: `./test_add.py:12` 의 assert 였다.
#:
#: 보안 에이전트가 검사해야 하는 것은 개발이 쓴 **구현**이지 기획이 쓴 테스트가
#: 아니다. bandit 자신의 문서도 테스트 디렉터리를 스캔 대상에서 빼라고 권한다.
_SECURITY_SCAN_EXCLUDE = "./test_*.py,./tests"


def run_security_scan(root: Path) -> ToolResult:
    return _run(
        root,
        [sys.executable, "-m", "bandit", "-r", ".", "-q", "-x", _SECURITY_SCAN_EXCLUDE],
    )


#: pytest 종료코드 중 "인수 테스트가 제구실을 한다"는 증거가 되는 값.
#: 1 = 테스트가 실패했다, 2 = 수집 중 에러(부를 대상이 없어 import 실패).
#: 구현이 하나도 없는 워크스페이스에서는 **둘 중 하나여야 정상**이다.
_MEANINGFUL_RED_EXITS = (1, 2)


def check_acceptance_tests(root: Path) -> ToolResult:
    """기획이 쓴 인수 테스트가 검사할 값어치가 있는지 판정한다.

    구현이 아직 하나도 없는 워크스페이스에서 그 테스트를 그대로 돌린다. 쓸 만한
    인수 테스트라면 **반드시 실패한다** — 부를 함수가 없기 때문이다(TDD 의 red
    단계). 통과한다면 그 테스트는 아무것도 검사하지 않는다는 뜻이고, 하나도
    수집되지 않는다면 검사할 것 자체가 없다는 뜻이다.

    **판정은 여기서도 모델이 하지 않는다.** 이 함수는 pytest 의 종료코드를
    뒤집어 옮길 뿐이다 — "테스트가 실패했다"(pytest 1)가 이 도구에서는
    "합격"(0)이고, "테스트가 통과했다"(pytest 0)가 "불합격"(1)이다.

    실측 근거(실제 LLM 실행 2회, 구현 없는 워크스페이스에서 측정):
      REQ-CART-095427  모듈 최상단 assert 만 있어 수집 0개  → pytest exit 5
      REQ-SITE-101435  본문이 `assert True`                → pytest exit 0 (4 passed)
    둘 다 개발이 받을 목표가 비어 있었고, 각각 환류 1회·3회를 헛돌았다.
    """
    inner = _run(root, [sys.executable, "-m", "pytest", "-q", "--no-header"])
    if inner.exit_code in _MEANINGFUL_RED_EXITS:
        return ToolResult(
            ok=True,
            exit_code=0,
            detail=(
                "확인 항목이 제구실을 한다: 구현이 없는 상태에서 예상대로 실패했다.\n"
                f"(pytest 종료코드 {inner.exit_code})\n{inner.detail}"
            ),
        )
    if inner.exit_code == 0:
        return ToolResult(
            ok=False,
            exit_code=1,
            detail=(
                "확인 항목이 아무것도 검사하지 않는다: 구현이 하나도 없는데 "
                "테스트가 전부 통과했다. 무엇을 만들어도 통과한다는 뜻이다.\n"
                "테스트 본문이 비어 있거나(`assert True`, `pass`) 검사할 함수를 "
                "import 하지 않았을 가능성이 높다.\n"
                f"{inner.detail}"
            ),
        )
    if inner.exit_code == 5:
        return ToolResult(
            ok=False,
            exit_code=1,
            detail=(
                "확인 항목이 하나도 수집되지 않았다. pytest 는 `def test_...()` "
                "형태의 함수만 테스트로 본다 — 모듈 최상단의 `assert` 는 세지 "
                "않는다.\n"
                f"{inner.detail}"
            ),
        )
    return ToolResult(
        ok=False,
        exit_code=1,
        detail=(
            f"확인 항목을 돌려보지 못했다 (pytest 종료코드 {inner.exit_code}).\n"
            f"{inner.detail}"
        ),
    )


#: ruff 에 넘기는 규칙 선택. **버그만 고른다.**
#:
#: 기본값이나 넓은 선택(E, W, I, DTZ …)을 쓰면 import 정렬·따옴표 스타일 같은
#: 취향 지적이 쏟아진다. 개발 에이전트는 그걸 고치느라 턴과 예산을 태우는데,
#: 그 지적들은 요구사항이 통과하는 것과 아무 관계가 없다.
#:
#: 실측으로 고른 셋(같은 파일에 넓은 선택을 걸면 10건이 나오는데 그중 8건이
#: 스타일이었다):
#:   F821  정의되지 않은 이름 — 오타 난 변수·함수. 실행하면 NameError 다.
#:   F401  쓰지 않는 import
#:   F841  값을 넣어 놓고 쓰지 않는 지역 변수 — 대개 잊은 코드다
#:   E9    문법 오류 — 파일이 아예 실행되지 않는다
#:
#: 멀쩡한 코드(add, reverse_string)에는 오탐이 없는 것도 확인했다.
_LINT_SELECT = "F,E9"

#: 검사에서 뺄 경로. `_SECURITY_SCAN_EXCLUDE` 와 같은 대상이지만 ruff 는
#: `--exclude` 를 인자마다 하나씩 받으므로 리스트로 따로 둔다.
_LINT_EXCLUDE = ("test_*.py", "tests")


def run_lint(root: Path) -> ToolResult:
    """문법 오류·오타 난 이름·죽은 코드를 찾는다.

    **판정 도구가 아니다.** 개발 에이전트가 자기 코드를 고치는 재료일 뿐이고,
    PASS/FAIL 은 여전히 QA 의 `run_tests` 와 보안의 `run_security_scan` 이
    정한다. 그래서 개발에게 준다 — 검증자에게 주면 판정 근거가 둘로 갈린다.

    인수 테스트는 검사에서 뺀다. 개발은 그 파일을 고칠 수 없으므로(`write_file`
    이 거부한다) 거기서 나온 지적은 고칠 방법이 없는 잔소리가 된다.
    """
    argv = [
        sys.executable, "-m", "ruff", "check",
        "--no-cache",     # 워크스페이스에 .ruff_cache 를 남기지 않는다
        "--isolated",     # 워크스페이스에 굴러다니는 설정 파일을 읽지 않는다
        "--select", _LINT_SELECT,
    ]
    for pattern in _LINT_EXCLUDE:
        argv += ["--exclude", pattern]
    argv.append(".")
    return _run(root, argv)
