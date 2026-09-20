"""역할별 프롬프트와 권한.

프롬프트 반복 개선은 범위 밖이다 (스펙 §14.3). 여기 있는 문장은 "모델을 잘
구슬리는" 것이 목적이 아니라 **역할의 계약을 적어 두는** 것이 목적이다.

`task_prompt` 의 `feedback` 항목은 8B 모델이 작성한 보고서 문자열에서
비롯된다. `verdict` 가 없거나 "FAIL"/"PASS" 가 아닌 값이거나 `summary` 가
없는, 형태가 망가진 항목이 나올 수 있다는 뜻이다. 이 모듈은 판정을 내리지
않으므로(그건 도구의 종료코드가 정한다) 그런 항목을 만나도 예외를 던지지
않는다: `verdict == "FAIL"` 이 아닌 항목(불명확한 값 포함)은 조용히
불만 목록에서 제외되고, `summary` 가 없으면 빈 문자열로 취급한다. 즉
망가진 항목은 "통과한 것처럼" 무시되지 "실패인데 못 보여준 것처럼"
숨겨지지 않는다 — 애매하면 보수적으로 조용히 넘어간다.
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
            lines.append(f"- [{item.get('agent', '?')}] {item.get('summary', '')}")
        lines.append("")
        lines.append("위 지적을 고쳐라.")
    return "\n".join(lines)
