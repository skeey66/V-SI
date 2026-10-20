"""판정 합치기. LLM 을 부르지 않는 결정적 노드다.

QA·보안이 모두 PASS 면 `accepted`. 하나라도 FAIL 이면 회차 상한 안에서
`remediating` (반려 사유를 개발에게 넘긴다), 상한을 넘기면 `escalated`.
"""
from __future__ import annotations

from lg_agent.state import VsiState

VERIFIERS = ("qa", "security")


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
    if revision >= state.get("max_revisions", 3):
        return {"status": "escalated", "feedback": failures}
    return {"status": "remediating", "revision": revision + 1, "feedback": failures}
