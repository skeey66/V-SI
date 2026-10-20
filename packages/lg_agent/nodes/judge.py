"""판정 합치기. LLM 을 부르지 않는 결정적 노드다.

QA·보안이 모두 PASS 면 `accepted`. 하나라도 FAIL 이면 회차 상한 안에서
`remediating` (반려 사유를 개발에게 넘긴다), 상한을 넘기면 `escalated`.

**반려 기억.** 실측(DIAG-91e1d7, "비밀번호를 md5 로 해시")에서 회차마다 판정이
번갈아 뒤집혔다: 1회차 보안 FAIL(md5 금지) → 개발이 sha256 으로 바꿈 → 2회차
QA FAIL(테스트는 md5 값을 기대) → 개발이 md5 로 되돌림 → 3회차 보안 FAIL.
개발이 직전 회차의 지적만 보기 때문이다. 그래서 이번 회차에 통과한 검증자의
이전 지적도 "다시 깨지 마라"는 표시와 함께 피드백에 남긴다.
"""
from __future__ import annotations

from lg_agent.state import VsiState

VERIFIERS = ("qa", "security")
#: 이전 회차 지적을 다시 넘길 때 요약 길이 상한 — 회차가 쌓여도 프롬프트가 불지 않게
CARRIED_SUMMARY_CHARS = 600
CARRIED_PREFIX = "(이전 회차 지적 — 지금은 통과했지만 다시 깨지 마라) "


def make_judge(memory: bool = True):
    def judge(state: VsiState) -> dict:
        verdicts = state.get("verdicts", {})
        revision = state.get("revision", 1)
        failures = [
            {"agent": v["agent"], "verdict": "FAIL", "summary": v["summary"]}
            for name in VERIFIERS
            if (v := verdicts.get(name)) and v["verdict"] != "PASS"
        ]
        missing = [name for name in VERIFIERS if name not in verdicts]
        if not failures and not missing:
            return {"status": "accepted", "feedback": []}

        feedback = list(failures)
        if memory:
            failing = {f["agent"] for f in failures}
            for old in state.get("feedback", []):
                if old["agent"] not in failing:
                    summary = old["summary"].removeprefix(CARRIED_PREFIX)
                    feedback.append({**old, "summary": CARRIED_PREFIX + summary[:CARRIED_SUMMARY_CHARS]})

        if revision >= state.get("max_revisions", 3):
            return {"status": "escalated", "feedback": feedback}
        return {"status": "remediating", "revision": revision + 1, "feedback": feedback}

    return judge


#: 기본 동작(반려 기억 켬). 단위 테스트와 기존 import 경로용.
judge = make_judge()
