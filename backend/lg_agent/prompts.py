"""LangGraph 경로 전용 프롬프트.

dev·qa·security 는 기존 `llm_agent.roles` 의 문장을 그대로 쓴다. 기획만 다르다:
기존 기획 프롬프트는 "마지막에 `check_acceptance_tests` 를 반드시 호출해라"를
담고 있는데, 이 경로에서는 게이트 노드가 그 도구를 직접 부르므로 기획에게 주지
않는다. 실측(REQ-LG-dc3113d3, REQ-LG-aebe2ac9, gpt-4o-mini)에서 그 문장을 남겨
두자 모델이 테스트 파일 끝에 `check_acceptance_tests()` 라는 파이썬 호출을
적었고, 개발은 테스트 파일을 고칠 수 없으니 3회차 내내 같은 NameError 로
반려됐다.
"""
from __future__ import annotations

PLANNER_TOOLS = ("write_file",)

PLANNER_SYSTEM = (
    "너는 기획 에이전트다. 요구사항을 받아 `write_file` 로 파일 두 개를 쓴다.\n"
    "1. `spec.md` — 무엇을 만들지, 구현 모듈 이름과 함수 시그니처를 포함한 간결한 명세\n"
    "2. `test_<모듈>.py` — 그 명세를 검사하는 pytest 인수 테스트\n\n"
    "규칙:\n"
    "- 구현은 하지 마라. 테스트 파일 안에 구현이나 빈 함수(스텁)를 정의하지 마라. "
    "구현 모듈(예: `cart.py`)에서 `from cart import ...` 로 import 해라.\n"
    "- 테스트는 `def test_...()` 함수로만 쓴다. 모듈 최상단에는 import 외에 어떤 "
    "코드도 두지 마라.\n"
    "- 본문에서 실제로 함수를 호출하고 기대값을 단언해라. `assert True`·`pass` 는 금지.\n"
    "- 표준 라이브러리만 쓴다.\n"
    "- 두 파일을 다 쓰면 짧게 무엇을 썼는지 답하고 끝내라. 테스트는 시스템이 직접 검사한다."
)


#: Prompt Hub 에 올릴 때 쓰는 이름
PLANNER_PROMPT_NAME = "vsi-planner"


class PromptStore:
    """기획 프롬프트의 현재 버전을 고른다.

    기본은 위의 로컬 문자열이다. 관리자가 LangSmith Prompt Hub 의 특정 커밋을
    활성화하면 그 버전을 쓴다 — 재배포 없이 프롬프트를 바꾸고, 어느 실행이 어느
    버전을 썼는지는 트레이스 메타데이터(`prompt_commit`)로 남는다.

    Hub 를 읽지 못하면(네트워크·키 문제) 로컬 기본값으로 돌아간다. 프롬프트
    저장소 장애가 요구사항 처리를 막으면 안 된다.
    """

    def __init__(self, client=None) -> None:
        self._client = client
        self.active_commit: str | None = None
        self._cache: dict[str, str] = {}

    def planner_system(self) -> str:
        commit = self.active_commit
        if not commit:
            return PLANNER_SYSTEM
        return self.template_of(commit) or PLANNER_SYSTEM

    def template_of(self, commit: str) -> str | None:
        """Hub 의 특정 커밋 본문. 커밋은 바뀌지 않으므로 한 번 읽으면 캐시한다."""
        if self._client is None:
            return None
        if commit not in self._cache:
            try:
                prompt = self._client.pull_prompt(f"{PLANNER_PROMPT_NAME}:{commit}")
                self._cache[commit] = prompt.messages[0].prompt.template
            except Exception:
                return None
        return self._cache[commit]
