#!/usr/bin/env bash
# 단일 명령 데모: 스택을 띄우고 요구사항 하나를 환류 2회 끝에 accepted까지 돌린다.
#
# 완료 정의 1번(스펙 10절)의 재현 절차다 — LLM 호출은 전혀 없다(completion
# criterion 5): 4개 에이전트는 전부 verdict를 YAML에서 읽는 스텁이다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose up -d --build

echo "postgres 준비 대기..."
until docker compose exec -T postgres pg_isready -U vsi >/dev/null 2>&1; do sleep 1; done

python -m orchestrator.cli start \
  --requirement REQ-DEMO \
  --title "학생 행사 신청" \
  --scenario scenarios/qa_fails_twice.yaml

echo
echo "그래프 UI : http://localhost:5173"
echo "Jaeger    : http://localhost:16686/api/traces?service=orchestrator&limit=5"
