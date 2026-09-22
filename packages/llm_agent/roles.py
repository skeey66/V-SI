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
    # 기획은 검증 에이전트가 아니다(qa/security 처럼 환류를 만들지 않는다).
    # 그런데 `verdict_tool` 은 갖는다 — 자기가 쓴 인수 테스트가 **검사할
    # 값어치가 있는지**를 도구에 물어야 하기 때문이다. 판정은 여기서도 모델이
    # 아니라 종료코드가 한다.
    "planner": Role(
        "planner", "requirements",
        ("write_file", "check_acceptance_tests"), False, "check_acceptance_tests",
    ),
    # 개발에게 `run_lint` 가 있는 것은 판정 권한이 아니다(verdict_tool 은 없다).
    # 문법 오류와 오타 난 이름은 실행해 보기 전에 알 수 있고, 그걸 QA 왕복으로
    # 발견하면 회차 하나가 통째로 날아간다. 검증자에게 주면 판정 근거가 둘로
    # 갈리므로 주지 않는다.
    "dev": Role(
        "dev", "source_code",
        ("list_files", "read_file", "write_file", "run_lint"), False, None,
    ),
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
        "테스트는 표준 라이브러리만 쓴다. 외부 패키지를 import 하지 마라.\n\n"
        "테스트는 `def test_...()` 형태의 함수로 써라. 모듈 최상단의 `assert` 는 "
        "pytest 가 테스트로 세지 않는다.\n"
        "본문을 비워두지 마라 — `assert True` 나 `pass` 만 있는 테스트는 무엇을 "
        "만들어도 통과하므로 개발 에이전트에게 아무 목표도 주지 못한다. 만들 "
        "함수를 `import` 해서 실제로 호출하고 결과를 단언해라.\n\n"
        "파일을 다 쓴 뒤 마지막에 `check_acceptance_tests` 를 반드시 호출해라. "
        "구현이 아직 없으므로 **테스트가 실패하는 것이 정상**이다 — 그게 "
        "테스트가 제구실을 한다는 증거다. 통과했다면 네 테스트가 아무것도 "
        "검사하지 않고 있다는 뜻이니 고쳐 쓰고 다시 호출해라."
    ),
    "dev": (
        "너는 개발 에이전트다. 워크스페이스의 인수 테스트를 읽고, 그것을 "
        "통과시키는 구현을 작성한다.\n\n"
        "먼저 `list_files` 와 `read_file` 로 테스트를 읽어라. **테스트 파일에는 "
        "쓸 수 없다** — `write_file` 이 거부한다. 테스트는 네가 통과시켜야 할 "
        "목표이지 고쳐 쓸 대상이 아니다. 구현 파일만 쓴다.\n"
        "표준 라이브러리만 쓴다. 외부 패키지를 import 하지 마라.\n\n"
        "파일을 쓴 뒤 `run_lint` 로 한 번 훑어라. 오타 난 변수 이름이나 문법 "
        "오류처럼 실행하면 바로 죽는 실수를 잡아 준다 — QA 에 넘기기 전에 "
        "고치면 회차 하나를 아낀다. 지적이 없으면 그대로 두면 된다."
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
