"""LLM-as-a-Judge 평가 (LangSmith 실험).

    # Docker 로 서비스를 띄운 뒤
    PYTHONPATH=backend python scripts/eval_judge.py

**왜 필요한가.** 이 서비스의 합격 판정은 pytest·bandit 종료코드다. 모델의 "됐다고
생각한다"는 막지만, **기획이 쓴 테스트가 요구사항을 덜 담으면 그대로 통과한다.**
그래서 "테스트 통과"와는 별개로, 결과물이 원래 요구사항을 얼마나 충실히 반영했는지를
LLM 심판에게 따로 채점시킨다.

평가자 세 개:
- `accepted`      (결정적) 완료 상태로 끝났는가
- `revisions`     (결정적) 몇 회차 만에 끝났는가 — 낮을수록 좋다
- `faithfulness`  (LLM 심판) 명세·테스트·구현이 요구사항을 충실히 반영했는가 (1~5 → 0~1)

대상(target)은 실행 중인 API(`/api/requirements`)를 부른다. 그래서 LLM 이 쓴 코드는
Docker 의 격리된 workspace 컨테이너에서만 돈다. 결과는 LangSmith 의
`vsi-eval` 데이터셋 실험으로 남는다.

비용: 요구사항 1건 약 $0.001~0.003 + 심판 1회 약 $0.0005. 기본 5건이면 $0.02 안쪽.
"""
from __future__ import annotations

import argparse
import time

import httpx
from langchain_openai import ChatOpenAI
from langsmith import Client
from pydantic import BaseModel, Field

from lg_agent.settings import load_settings

DATASET = "vsi-eval"
EXAMPLES = [
    "장바구니에 담긴 상품 금액을 모두 합치고, 합계가 5만원 이상이면 배송비 3000원을 빼주세요",
    "문자열이 회문인지 판별하는 함수 (대소문자와 공백은 무시)",
    "정수 리스트에서 두 번째로 큰 값을 돌려주는 함수, 값이 부족하면 None",
    "섭씨 온도 리스트를 화씨로 바꾸되, 절대영도(-273.15도)보다 낮은 값이 있으면 ValueError",
    "이메일 주소 문자열이 '아이디@도메인.최상위' 형식인지 검사하는 함수, 공백이 있으면 False",
]

JUDGE_PROMPT = """너는 소프트웨어 납품물을 검수하는 시니어 개발자다.
아래 요구사항과, AI 개발팀이 만든 명세·인수 테스트·구현을 읽고
**결과물이 요구사항을 얼마나 충실히 반영했는지** 1~5점으로 채점해라.

채점 기준:
5 = 요구사항의 모든 조건과 경계값을 테스트가 검사하고, 구현도 요구사항대로 동작한다
4 = 핵심 조건은 모두 반영됐지만 경계값 한두 개를 테스트가 놓쳤다
3 = 주요 조건 하나가 테스트에서 빠졌거나, 구현이 요구사항과 일부 다르다
2 = 테스트가 요구사항의 일부만 검사해서, 통과해도 요구사항을 만족한다고 보기 어렵다
1 = 요구사항과 다른 것을 만들었다

테스트 통과 여부는 이미 따로 확인했다. 너는 "요구사항 대비 충실도"만 본다.

[요구사항]
{requirement}

[결과물]
{files}
"""


class Verdict(BaseModel):
    score: int = Field(ge=1, le=5, description="1~5점")
    reason: str = Field(description="점수의 근거를 한두 문장으로")


def make_target(api: str):
    def target(inputs: dict) -> dict:
        with httpx.Client(base_url=api, timeout=30) as http:
            rid = http.post("/api/requirements", json={"title": inputs["requirement"]}).json()["requirement_id"]
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                detail = http.get(f"/api/requirements/{rid}").json()
                if detail["status"] in ("accepted", "escalated", "error", "blocked"):
                    break
                time.sleep(3)
            files = {}
            for f in http.get(f"/api/requirements/{rid}/files").json():
                files[f["path"]] = http.get(f"/api/requirements/{rid}/files/{f['path']}").json()["content"]
        return {"requirement_id": rid, "status": detail["status"],
                "revision": detail["revision"], "files": files}

    return target


def accepted(run, example) -> dict:
    return {"key": "accepted", "score": int(run.outputs.get("status") == "accepted")}


def revisions(run, example) -> dict:
    return {"key": "revisions", "score": run.outputs.get("revision") or 0}


def make_judge(model: str):
    judge = ChatOpenAI(model=model, temperature=0).with_structured_output(Verdict)

    def faithfulness(run, example) -> dict:
        files = run.outputs.get("files") or {}
        if not files:
            return {"key": "faithfulness", "score": 0, "comment": "결과물 파일이 없다"}
        body = "\n\n".join(f"--- {path} ---\n{text[:4000]}" for path, text in sorted(files.items()))
        verdict = judge.invoke(JUDGE_PROMPT.format(requirement=example.inputs["requirement"], files=body))
        # 1~5 → 0~1 (LangSmith 에서 다른 점수와 같은 눈금으로 보이게)
        return {"key": "faithfulness", "score": (verdict.score - 1) / 4, "comment": f"{verdict.score}점 — {verdict.reason}"}

    return faithfulness


def ensure_dataset(client: Client) -> None:
    if client.has_dataset(dataset_name=DATASET):
        return
    ds = client.create_dataset(DATASET, description="V-SI 요구사항 충실도 평가 (LLM-as-a-Judge)")
    client.create_examples(dataset_id=ds.id, inputs=[{"requirement": r} for r in EXAMPLES])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:5180")
    p.add_argument("--judge-model", default="gpt-4o-mini")
    p.add_argument("--prefix", default="vsi-langgraph")
    a = p.parse_args()

    load_settings()
    client = Client()
    ensure_dataset(client)
    results = client.evaluate(
        make_target(a.api), data=DATASET,
        evaluators=[accepted, revisions, make_judge(a.judge_model)],
        experiment_prefix=a.prefix, max_concurrency=3,
        metadata={"judge_model": a.judge_model},
    )
    rows = list(results)
    for r in rows:
        scores = {e.key: e.score for e in r["evaluation_results"]["results"]}
        comment = next((e.comment for e in r["evaluation_results"]["results"] if e.key == "faithfulness"), "")
        print(f"- {r['example'].inputs['requirement'][:40]} | {scores} | {comment}")
    print("실험:", results.experiment_name)


if __name__ == "__main__":
    main()
