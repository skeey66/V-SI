# V-SI : LangGraph 기반 멀티에이전트 가상 SI 개발팀 — 결과 보고서

> AI 캠퍼스 미니프로젝트 · 과제 2 (신규 프로젝트) · 브랜치 `feat/langgraph`

자연어 요구사항 하나를 받으면 **기획 → 개발 → QA·보안(병렬) → 판정** 네 에이전트가 협업해
코드를 만들고, 실제 `pytest`·`bandit` 으로 검증하고, 반려되면 스스로 고친다.
기존에 직접 만들었던 V-SI(A2A 프로토콜 + 자체 오케스트레이터 + 로컬 Ollama)의 협업
흐름을 LangGraph 로 옮기고, MCP·RAG·스트리밍·관리자 기능을 붙였다. 기존 경로는 지우지
않고 Before 비교군으로 남겼다.

---

## 1. 요구사항 반영

| 요구사항 | 구현 | 위치 |
|---|---|---|
| LangChain·LangGraph 멀티에이전트 | `StateGraph` 7노드. 에이전트 4종은 `create_agent`(ReAct) 하위 그래프. QA·보안 병렬 fan-out/fan-in, 조건부 엣지로 반려 루프, `interrupt()` 로 사람 승인 | `packages/lg_agent/graph.py`, `nodes/` |
| MCP 서버 | ① 도구 서버(기존): 역할마다 별도 MCP 인스턴스 → 개발에게는 `run_tests` 가 **존재하지 않음** ② 지식 서버(신규): RAG 검색 tool + `guide://security/{id}` resource | `services/tool_server`, `services/knowledge_server` |
| Vue UI | Vue 3 + vue-router. 요청·실행 상세(파이프라인·타임라인·실시간 출력·승인·결과물·피드백)·관리자 | `web-vue/` |
| FastAPI REST | lifespan 에서 그래프·체크포인터 1회 생성, 요구사항·승인·재개·파일·피드백·관리자 API | `packages/lg_api/` |
| 스트리밍 응답 | SSE. 노드 시작/종료·도구 호출·판정·RAG·**LLM 토큰**을 실시간 전송, `?after=` 재접속 | `lg_api/runs.py`, `web-vue/src/api/sse.js` |
| LangSmith 관리자 | 트레이스 기반 실행 수·성공률·지연(p95)·토큰·비용 집계, 실행별 트레이스 링크, 👍/👎 피드백 기록, **Prompt Hub 프롬프트 버전 push·활성화**(재배포 없이 교체), RAG·회차 상한 런타임 토글 | `lg_api/admin.py`, `web-vue/src/views/AdminView.vue` |
| (평가 항목) RAG | bandit 규칙별 조치 가이드 25개 → FAISS + `text-embedding-3-small`, 규칙 ID 정확 매칭 + 의미 검색 + 유사도 임계값 | `knowledge_server/index.py` |
| (평가 항목) 의존성 주입 | 노드는 LLM·MCP 를 직접 만들지 않고 `Deps` 로 받는다 → 가짜 LLM·가짜 도구로 그래프 전체를 네트워크 없이 시험 | `lg_agent/deps.py` |
| (평가 항목) 단위 테스트 | 파이썬 22개(그래프 흐름 10 · API 7 · RAG 인덱스 5) + 프런트 11개(SSE 파서 7 · 파이프라인 4) — 가짜 LLM 이라 실제 API 비용 0원 | `tests/lg_agent`, `tests/lg_api`, `tests/knowledge_server`, `web-vue/src/**/*.test.js` |

## 2. 구조

```
Vue 3 ──SSE/REST──▶ FastAPI (lg_api) ── LangSmith (추적·피드백·Prompt Hub)
                        │
                 LangGraph StateGraph ── Postgres 체크포인터
   planner → gate ─┬─────────────▶ dev ─┬─▶ qa ───────┬─▶ judge ─┬─▶ END (accepted/escalated)
                   └─▶ approval ───┘     └─▶ security ─┘          └─▶ dev (반려 + 사유)
                      (interrupt)               │
                        │ MCP (역할별 경로)       │ MCP
                        ▼                       ▼
             도구 서버 (internal 망, 인터넷 없음)   지식 서버 (FAISS RAG)
             write/read/run_tests/bandit/ruff
```

**판정은 모델이 내리지 않는다.** QA·보안 노드가 판정 도구를 **직접** 부르고 종료코드로
PASS/FAIL 을 정한다. LLM 은 FAIL 일 때만, 실패 출력을 개발이 고칠 수 있는 문장으로
옮기는 데 쓴다. 기존 경로에서는 모델이 판정 도구를 안 부르고 끝내는 실수를 최대 2번
재촉해야 했는데(`MAX_VERDICT_NUDGES`), 이 구조에서는 그 실수가 일어날 수 없다.

## 3. 문제 → 개선 → 실측 (Before / After)

모델은 전부 `gpt-4o-mini`, 비용은 LangSmith 집계 및 토큰 × 단가로 계산했다.
원자료: `docs/lg/bench-planner.json`, `docs/lg/bench-rag.json`, 스크립트 `scripts/bench_lg.py`.

### 3-1. 기획 프롬프트 — 도구 이름을 코드로 적는 문제

**문제.** 기존 기획 프롬프트의 "마지막에 `check_acceptance_tests` 를 반드시 호출해라"를
gpt-4o-mini 가 **테스트 파일 끝에 `check_acceptance_tests()` 라는 파이썬 줄을 쓰는 것**으로
해석했다. 개발은 테스트 파일을 고칠 수 없으므로 3회차 내내 같은 `NameError` 로 반려됐다.
게이트도 이걸 못 잡았다 — 수집 오류(pytest 2)는 "구현 모듈이 아직 없다"는 정상 red 와
같은 종료코드다.

**개선.** 게이트 노드가 그 도구를 직접 부르므로 기획에게서 도구를 빼고, 전용 프롬프트에
"테스트 파일에 구현·스텁을 두지 말고 모듈에서 import" 규칙을 넣었다.

| 3개 요구사항 × 2회 | Before (기존 프롬프트) | After |
|---|---|---|
| 완료율 | **0 / 6** | **6 / 6** |
| 평균 회차 | 3.0 (전부 상한) | 1.17 |
| 평균 지연 | 45.6초 | 12.1초 |
| 평균 토큰 | 29,451 | 5,162 |
| 평균 비용 | $0.0056 | $0.0010 |

### 3-2. 보안 RAG + 반려 기억 — 보안 지적을 못 고치는 문제

**문제.** 요구사항이 위험한 방식을 지시하면(셸 명령, `eval`, md5 …) bandit 이 FAIL 하고,
개발은 "왜·어떻게" 고칠지 몰라 회차를 소진했다.

**개선 ①: RAG.** 보안 FAIL 이면 bandit 출력의 규칙 ID·문제 문장으로 조치 가이드를 검색해
설명과 개발 피드백에 붙인다.

**진단 중 발견한 진짜 원인: 판정 진동.** md5 요구사항을 회차별로 보니
`1회차 보안 FAIL → 개발이 sha256 으로 변경 → 2회차 QA FAIL(테스트는 md5 값 기대) →
md5 로 되돌림 → 3회차 보안 FAIL` 로 판정이 번갈아 뒤집혔다. 개발이 **직전 회차 지적만**
보기 때문이다. 가이드가 아무리 정확해도 이 진동은 못 막는다.

**개선 ②: 반려 기억.** 판정 노드가 이번에 통과한 검증자의 이전 지적도 "다시 깨지 마라"
표시와 함께 넘기고(600자 상한), 개발 프롬프트에 "모든 지적을 동시에 만족하라, 테스트
기대값은 유지하라"를 붙였다.

| 보안 함정 5문항 × 2회 | 기본 | +RAG | +반려 기억 | +RAG +반려 기억 |
|---|---|---|---|---|
| 완료율 | 4/10 | 5/10 | 5/10 | 5/10 |
| ㄴ 위험 방식을 **지시하는** 3문항 | 0/6 | 1/6 | 1/6 | **2/6** |
| 평균 토큰 | 16,772 | 21,212 | 18,532 | 21,439 |
| 평균 비용 | $0.0032 | $0.0040 | $0.0036 | $0.0041 |
| 평균 지연 | 27.6초 | 31.2초 | 29.9초 | 32.1초 |

**해석(정직하게).** 표본이 조건당 10회라 전체 완료율 차이(40→50%)는 오차 범위다. 효과가
보이는 곳은 "위험한 방식을 지시하는" 문항(0/6 → 2/6)이고, 대가로 토큰이 약 28% 늘었다.
**md5 문항은 어떤 조건에서도 풀리지 않았다** — 요구사항("md5 로 해시")과 보안 정책이
정면으로 충돌하는 경우는 에이전트가 풀 문제가 아니라 사람이 정할 문제다(4장 참고).

### 3-3. 토큰 스트리밍 — 하위 그래프 토큰이 안 나오는 문제

`create_agent` 는 하위 그래프라서 `graph.astream(stream_mode="messages")` 만으로는 LLM 토큰이
바깥으로 나오지 않았다(최소 재현: 0개). `subgraphs=True` 로 바꾸자 같은 실행에서 토큰
이벤트가 흘렀다 (Docker 실측: 요구사항 1건에 `token` 이벤트 760개).

### 3-4. 기존 구조 대비

| | Before: A2A + 자체 오케스트레이터 | After: LangGraph |
|---|---|---|
| 오케스트레이션 코드 (주석·빈 줄 제외) | 3,221줄 (`orchestrator`·`llm_agent`·`agent_entry`·`event_gateway`) | 1,111줄 (`lg_agent`·`lg_api`) |
| 요구사항 1건 처리 시간 | 10~20분 (README, 로컬 qwen3:8b) | 10~30초 (gpt-4o-mini) — *모델이 달라 직접 비교 불가* |
| 크래시 복구 | 리컨실러가 **자동**으로 재디스패치 | Postgres 체크포인트에서 `/resume` 으로 **수동** 재개 — SIGKILL 실측 시 `dev` 부터 이어 accepted, 끝난 `planner` 는 다시 안 돎 |
| 진행 이벤트 | DB 아웃박스 → 게이트웨이 (재시작해도 보존) | 프로세스 메모리 (재시작 시 타임라인 소실) |
| 에이전트 격리 | 컨테이너 4개, 독립 배포 | 한 프로세스 안의 노드 |

## 4. 한계와 트레이드오프

- **복구가 자동이 아니다.** 체크포인터는 상태를 지키지만 누가 다시 깨울지는 정하지 않는다.
  기존 리컨실러가 하던 일을 `/resume` 호출로 대신한다.
- **이벤트 기록이 휘발성이다.** SSE 타임라인은 메모리에만 있어 API 재시작 시 사라진다.
  결과(상태·판정·파일)는 체크포인트와 워크스페이스에 남는다.
- **격리·확장성 후퇴.** 네 에이전트가 한 프로세스의 노드가 되면서 에이전트별 독립 배포·
  수평 확장이 불가능해졌다. 대신 코드가 1/3 로 줄고 흐름이 그래프 한 장으로 읽힌다.
  (LLM 이 쓴 코드를 실행하는 도구 서버의 `internal` 망 격리는 그대로다.)
- **RAG 의 비용.** 임베딩은 문서 해시로 캐시해 25개 전체가 1회 약 $0.0001 이지만, 검색 결과를
  프롬프트에 넣으면서 실행당 토큰이 약 28% 늘었다. 관련 없는 문서가 섞이지 않도록 유사도
  임계값(0.35)을 두었고, 규칙 ID 가 있으면 의미 검색보다 정확 매칭을 우선한다.
- **요구사항과 정책의 충돌.** md5 처럼 요구사항 자체가 정책 위반이면 에이전트는 회차 상한까지
  헛돈다(실행당 약 $0.005 낭비). 회차 상한이 손실을 묶어 둘 뿐 해결하지는 못한다.
- **무한 루프 방지는 층으로 쌓았다.** 회차 상한(3) · 에이전트 턴 상한(8, `recursion_limit`) ·
  에이전트 시간 예산(300초) · 도구 호출 상한(90초) · 응답 출력 토큰 상한(2,000).
- **판정 = 테스트 품질.** 종료코드 판정은 모델의 "통과했다고 생각한다"를 막지만, 테스트가
  나쁘면 그대로 통과한다. 게이트가 빈 테스트는 잡지만 수집 오류로 망가진 테스트는 못 잡는다(3-1).
- **라이브러리 호환성.** `langchain-mcp-adapters` 최신판은 `mcp==2.2.0` 에서 import 오류가 나
  (`RequestContext` 이동) 얇은 어댑터를 직접 만들었다. 공식 어댑터의 콜백·리소스 로딩은 못 쓴다.
- **LangSmith 의존.** 관리자 화면의 숫자는 외부 서비스에서 온다 — 조회 상한 100건, 무료 플랜
  월 5천 트레이스. 장애 시 관리자 화면은 비지만 요구사항 처리는 계속된다(프롬프트도 로컬 기본값으로 폴백).

## 5. 추가 개선 사항

1. **기획 단계에서 정책 충돌 감지** — 요구사항이 위험 방식을 지시하면 개발 전에 `interrupt()` 로
   사람에게 묻는다. md5 같은 헛도는 회차를 0으로 만들 수 있다.
2. **이벤트 영속화** — SSE 이벤트를 Postgres 에 남겨(기존 아웃박스 재사용) 재시작 후에도 타임라인 복원.
3. **기동 시 자동 재개** — lifespan 에서 `next` 가 남은 체크포인트를 찾아 `/resume` 을 스스로 호출.
4. **과거 성공 사례 RAG** — accepted 된 명세·테스트 쌍을 기획 few-shot 으로 검색.
5. **LangSmith 데이터셋 평가** — 위 벤치마크를 데이터셋 + LLM-as-judge 평가로 옮겨 프롬프트 버전별 자동 비교.
6. **모델 티어링** — 설명만 하는 검증자는 더 싼 모델, 개발은 더 강한 모델.

## 6. 비용

개발·시연·벤치마크 전체 OpenAI 사용량: **74회 실행, 약 126만 토큰, $0.22** (LangSmith 집계).
정상 요구사항 1건은 약 $0.001.

## 7. 실행

```bash
# Docker (권장)
docker compose --profile lg up -d --build postgres workspace knowledge lg-api web-vue
open http://localhost:5180

# 로컬 (개발용) — 주의: 도구 서버를 호스트에서 직접 띄우면 LLM 이 쓴 코드가 격리 없이
# 내 컴퓨터에서 실행된다(.env 파일도 읽을 수 있는 위치). 시연·측정은 Docker 로 한다 —
# Docker 의 workspace 컨테이너에는 키가 없고 인터넷도 없다.
PYTHONPATH=packages:services VSI_WORKSPACE_BASE=$PWD/.workspaces \
  VSI_MCP_ALLOWED_HOSTS=localhost:8010,127.0.0.1:8010 uvicorn tool_server.main:app --port 8010 &
PYTHONPATH=packages:services uvicorn knowledge_server.main:app --port 8011 &
PYTHONPATH=packages:services VSI_MCP_BASE_URL=http://localhost:8010 uvicorn lg_api.main:app --host 127.0.0.1 --port 8200 &
cd web-vue && npm install && npm run dev        # http://localhost:5180

# 시험 (실제 API 를 부르지 않는다)
.venv/bin/python -m pytest tests/lg_agent tests/lg_api tests/knowledge_server
```

### 키 보안 점검 결과 (2026-10-20)

| 경로 | 결과 |
|---|---|
| Git 이력 (전 브랜치) | `.env` 미추적, 두 키 모두 0건 |
| 작업 파일·Vue 빌드 결과물·로그 | 0건 |
| Docker 이미지 | `.env` 없음 (Dockerfile 이 `packages`·`services`·`scenarios` 만 복사) |
| 컨테이너 환경변수 | 키는 `lg-api`·`knowledge` 에만. **LLM 코드가 실행되는 `workspace` 에는 없음** |
| LangSmith 트레이스 | 키 원문 0건. LangChain 이 키를 `{"type": "secret", "id": ["OPENAI_API_KEY"]}` 로 직렬화. 초기 401 오류 트레이스에 OpenAI 가 가린 형태(앞 8자·뒤 4자)만 남음 |
| 포트 노출 | **수정함** — `lg-api`·`knowledge`·`web-vue` 를 `127.0.0.1` 에만 바인딩. 이전에는 같은 네트워크의 누구나 인증 없이 요청을 넣어 OpenAI 한도를 쓸 수 있었다 |

`.env` 에 `OPENAI_API_KEY`(또는 `OPENAPI_APIKEY`)와 `LANGSMITH_API_KEY`(또는 `LANGSMITH_APIKEY`).

## 부록: 화면

| 실행 중 (SSE 실시간) | 실행 완료 | 관리자 (LangSmith) |
|---|---|---|
| ![](img/run-live.png) | ![](img/run-detail.png) | ![](img/admin.png) |
