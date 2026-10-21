# LangGraph API 계약 (lg_api)

기본 주소 `http://localhost:8200`. Vue 개발 서버는 `/api` 를 이 주소로 프록시한다.

## 요구사항

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/api/requirements` | `{"title": str}` | 201 `{"requirement_id", "title", "status"}` — 그래프는 백그라운드에서 시작된다 |
| GET | `/api/requirements` | | `[{"requirement_id","title","status","revision","created_at","updated_at"}]` 최신순 |
| GET | `/api/requirements/{id}` | | `{"requirement_id","title","status","revision","verdicts":{agent: Verdict},"history":[...],"gate":{"ok","detail"}|null,"pending_approval": {"detail"}|null,"langsmith_urls":[str]}` |
| GET | `/api/requirements/{id}/events` | `?after=<seq>` (선택) | **SSE** (아래) |
| POST | `/api/requirements/{id}/approve` | `{"approved": bool}` | 202 `{"ok": true}` — `interrupt` 로 멈춘 그래프를 재개 |
| POST | `/api/requirements/{id}/resume` | | 202 `{"ok": true, "next": [노드]}` — API 가 죽어 멈춘 실행을 Postgres 체크포인트에서 이어감 |
| GET | `/api/requirements/{id}/files` | | `[{"path": str}]` |
| GET | `/api/requirements/{id}/files/{path}` | | `{"path", "content"}` |
| POST | `/api/requirements/{id}/feedback` | `{"score": 1 or 0, "reasons": [str]?, "comment": str?}` — 1 = 승인, 0 = 보완 요청. `reasons` 는 `요구사항과 다름`·`테스트 부족`·`코드 품질`·`보안 우려`·`너무 느림` 중에서 | 저장된 검수 의견 `{"score","reasons","comment","created_at"}` — LangSmith 피드백(key=`user_score`)으로 기록 |
| GET | `/api/requirements/{id}/feedback` | | `{"feedback": {...} or null}` — 이미 남긴 검수 의견 |

`Verdict = {"agent","verdict":"PASS"|"FAIL","exit_code","summary","revision"}`

`status` 값: `planned` `implementing` `verifying` `remediating` `blocked` `accepted` `escalated` `error`

## SSE 이벤트

`GET /api/requirements/{id}/events` 는 처음부터 모든 이벤트를 재생한 뒤 실시간으로 이어진다.
각 이벤트는

```
event: <type>
data: {"seq": 12, "type": "<type>", "ts": 1760000000.12, ...필드}

```

15초마다 `: ping` 주석 줄이 온다. 종료 상태(`done`, `error`) 이벤트 뒤 스트림이 닫힌다.
승인 대기(`interrupt`) 중에는 닫히지 않고 기다린다.

| type | 필드 |
|---|---|
| `run_started` | `requirement_id`, `title` |
| `agent_start` | `agent` (planner/dev/qa/security), `revision` |
| `token` | `agent`, `text` — LLM 출력 토큰 조각 |
| `tool_call` | `agent`, `tool`, `args` |
| `tool_result` | `agent`, `tool`, `ok`, `exit_code` |
| `node_end` | `node` (planner/gate/approval/dev/qa/security/judge), `status` (있으면) |
| `gate` | `ok`, `detail` |
| `verdict` | `agent`, `verdict`, `exit_code`, `summary`, `revision` |
| `rag` | `agent`, `query`, `hits`: `[{"id","title","score"}]` |
| `interrupt` | `detail` — 사람 승인 필요 |
| `resumed` | `approved` |
| `done` | `status`, `revision`, `elapsed_s` |
| `error` | `message` |

## 관리자 (LangSmith)

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/api/admin/config` | `{"model","max_revisions","rag_enabled","langsmith_project","langsmith_url"}` |
| PUT | `/api/admin/config` | 요청 `{"rag_enabled"?: bool, "max_revisions"?: int}` → 갱신된 config |
| GET | `/api/admin/summary` | `{"runs","success_rate","avg_latency_s","p95_latency_s","total_tokens","total_cost","avg_cost","by_status":{status: n},"feedback":{"up","down"}}` |
| GET | `/api/admin/feedback?limit=30` | `[{"score","reasons","comment","created_at","run_id","requirement_id","title","url"}]` 최신순 |
| GET | `/api/admin/runs?limit=50` | `[{"run_id","requirement_id","title","status","error","start_time","latency_s","total_tokens","total_cost","url","feedback_score"}]` |
| GET | `/api/admin/prompts/planner` | `{"name","active_commit": str|null,"commits":[{"commit_hash","created_at","preview","template"}],"local_default": str}` |
| POST | `/api/admin/prompts/planner` | 요청 `{"template": str}` → `{"commit_hash"}` (Prompt Hub 에 새 버전 push) |
| POST | `/api/admin/prompts/planner/activate` | 요청 `{"commit_hash": str|null}` (null = 로컬 기본값) → `{"active_commit"}` |
