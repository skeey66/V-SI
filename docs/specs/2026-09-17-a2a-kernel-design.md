# V-SI · SP1 A2A 커널 설계

작성일: 2026-09-17
상태: 승인 대기
범위: SP1 (A2A 커널). SP2·SP3는 별도 스펙.

## 1. 배경과 목표

V-SI는 기획·개발·QA·보안 역할의 AI 에이전트가 협업해 소프트웨어를 만드는 "가상 SI 회사"다.
원 과제는 초기 요구 정의이며, 본 프로젝트는
그 초기 요구 정의를 참조 규격으로 삼되 **포트폴리오 목적**으로 수행한다. 따라서 초기 요구 정의의 8개 목표를
전부 따르지 않고, 증명하려는 역량에 맞춰 범위를 재구성한다.

**증명 대상: 에이전트 인프라 · 분산 시스템 설계.**
에이전트의 프롬프트 품질이나 생성 코드의 우수성이 아니라, 여러 자율 프로세스가 프로토콜 위에서
협업하고 실패로부터 복구되는 구조를 설계·구현할 수 있음을 보인다.

### SP1이 달성해야 할 것

LLM을 전혀 호출하지 않고 오케스트레이션 전 구간이 동작하며, 그 동작이 관측 가능하고,
프로세스가 죽어도 복구된다.

## 2. 범위

### 포함 (SP1)

- A2A 서버로 동작하는 에이전트 4종 — 단 **LLM 없는 스텁**
- 중앙 오케스트레이터: 작업 의존성, 환류 루프, 반복 상한, 승인 게이트
- Task 라이프사이클과 Artifact 저장·버전·소유권
- 상태 지속(PostgreSQL), 재시도, 멱등성, 타임아웃 계층
- 크래시 복구를 위한 리컨실리에이션 루프
- 관측성: OpenTelemetry → Jaeger (시각화 A)
- 실시간 에이전트 그래프 UI (시각화 B)

### 제외

| 항목 | 이유 | 이관 |
|---|---|---|
| MCP 도구 서버, 실제 LLM 에이전트, Docker 코드 격리 실행 | SP1은 인프라 검증이 목적 | SP2 |
| 대시보드 지표, 단일 vs 다중 비교 실험 | 측정은 시스템이 돈 뒤 | SP3 |
| Task 라이프사이클 칸반 보드, 환류 시퀀스 다이어그램 | 동일 이벤트 스트림 재사용 가능 | SP3 |
| gRPC 바인딩, A2A SSE 스트리밍 | HTTP+JSON + push로 충분 | 필요 시 |
| 부하·성능 테스트 | 에이전트 4개, 동시 실행 1건 | 범위 밖 |

## 3. 기술 스택

초기 요구 정의 "구현 요건 · 권장 기술"을 따른다.

| 항목 | 선택 | 초기 요구 정의 |
|---|---|---|
| 언어 | Python 3.11 | Python 3.10 이상 ✓ |
| API 서버 | FastAPI | FastAPI ✓ |
| 에이전트 통신 | `a2a-sdk` **1.1.2** (A2A 스펙 1.0), HTTP+JSON 바인딩 | A2A Python SDK 1.0 ✓ |
| 상태 관리 | PostgreSQL (`DatabaseTaskStore`, SQLAlchemy async) | SQLite 또는 PostgreSQL ✓ |
| 관측성 | OpenTelemetry → Jaeger | OpenTelemetry ✓ |
| 대시보드 | React (실시간 그래프) | React 또는 Streamlit ✓ |
| 단위 테스트 | pytest | pytest ✓ |
| 컨테이너 | Docker / docker compose | Docker ✓ |

`a2a-sdk[fastapi,http-server,telemetry,postgresql]` extras를 사용한다.

**`[telemetry]` extra는 `opentelemetry-api`/`-sdk`만 설치하며 HTTP 계측을 포함하지 않는다.**
크로스 서비스 트레이스 전파를 위해 다음을 직접 추가한다 (2026-09-17 스파이크에서 확인):

- `opentelemetry-instrumentation-fastapi` — 서버 측 컨텍스트 추출
- `opentelemetry-instrumentation-httpx` — 클라이언트 측 컨텍스트 주입
- `opentelemetry-exporter-otlp` — Collector 전송

SP2에서 추가: MCP Python SDK, Ruff, mypy, Bandit, Semgrep, Playwright.

### 초기 요구 정의에 없는 설계 판단

**트랜잭셔널 아웃박스와 Event Gateway는 초기 요구 정의에 없는 본 설계의 추가다.**
실시간 그래프 UI(시각화 B)에 이벤트를 공급해야 하는데, 상태 전이와 이벤트 발행을 분리하면
"상태는 바뀌었는데 이벤트는 유실" 상태가 생긴다. 두 쓰기를 한 트랜잭션에 넣으면 이 창이
구조적으로 닫힌다. UI 편의가 아니라 정합성을 위한 선택이다.

## 4. 아키텍처

docker compose로 10개 서비스를 띄운다 — `orchestrator`, `planner`, `dev`, `qa`, `security`,
`postgres`, `event-gateway`, `web`(React), `otel-collector`, `jaeger`.

```
[React Graph UI] [Jaeger UI]
 │ WebSocket │
[Event Gateway] [OTel Collector] ←╌╌ 전 서비스 계측
 │ outbox tail ↑
 └──────────┐ ╎
 [Orchestrator] ╌╌╌╌╌╌┘
 │ A2A · HTTP+JSON · push callback
 ┌────────┬───┴────┬────────┐
 [planner] [dev] [qa] [security] :8001~:8004
 └────────┴────────┴────────┘
 │
 [PostgreSQL]
 workflow_* · artifacts · events(outbox) · agent_registry
```

### 결정과 근거

**허브-스포크.** 에이전트끼리 직접 호출하지 않고 전부 오케스트레이터를 경유한다. A2A 스펙은
메시 통신도 허용하지만, 초기 요구 정의가 요구한 "작업 소유권과 결과물 버전 관리"는 조정자가 한 곳일 때만
성립한다. 재시도 권한도 오케스트레이터만 갖는다.

**Event Gateway 분리.** 오케스트레이터에 WebSocket을 붙이지 않는다. UI가 죽거나 소켓이 밀려도
워크플로가 영향받지 않아야 한다.

**HTTP+JSON.** 초기 요구 정의 권장이며 curl로 재현 가능해 디버깅이 쉽다.

**메시지 브로커 없음.** 아웃박스 테일링으로 충분하다. Kafka/Redis를 넣으면 서비스가 늘고
운영 복잡도만 증가한다.

## 5. 컴포넌트 경계

### 에이전트 프로세스 (× 4)

| 계층 | 소유 |
|---|---|
| FastAPI 앱 인스턴스 | **우리** |
| `create_agent_card_routes()` / `create_jsonrpc_routes()` / `create_rest_routes()` → `add_a2a_routes_to_fastapi(app, ...)` | SDK (조립은 우리) |
| `DefaultRequestHandler` — Task 생성·상태 전이·이벤트·push notification | SDK |
| `DatabaseTaskStore` → PostgreSQL — Task 지속 | SDK (설정만 우리) |
| `AgentExecutor` — **유일한 확장 지점** | 우리 |
| `AgentCard` 선언 — skills, `security_schemes`, `security_requirements`, `capabilities.streaming`, `capabilities.push_notifications`, I/O modes | 우리 |
| `agent-runtime` — 앱 생성, 라우트 조립, OTel 계측, 헬스체크, 에러 매핑 | 우리 (얇게) |

SDK는 ASGI 애플리케이션을 만들어 주지 않고 **라우트를 우리 FastAPI 앱에 붙이는 방식**이다
(1.1.2 기준. `A2AFastAPIApplication` 같은 팩토리 클래스는 존재하지 않는다). 앱을 우리가 소유하므로
`FastAPIInstrumentor` 계측과 헬스체크 엔드포인트를 `agent-runtime`에서 자연스럽게 추가할 수 있다.

`agent-runtime`은 의도적으로 얇다. Task 지속·라이프사이클·전송 계층이 전부 SDK 소관이므로
공용 래퍼가 할 일은 기동 보일러플레이트와 계측뿐이다. 이 경계를 넘어 SDK 기능을 재구현하지 않는다.

### 오케스트레이터 (전부 우리 코드)

| 컴포넌트 | 책임 |
|---|---|
| Workflow Engine | 요구사항 ID → DEV/QA/SEC 하위작업 의존성 계산. 결정적 코드 |
| Policy Gate | 최대 반복 횟수, 최대 실행시간, 사람 승인 필요 작업 차단 |
| Push Receiver | 에이전트 완료 콜백 수신. 폴링 루프 없음 |
| Artifact Registry | Artifact 버전·소유권 관리 |
| Outbox Writer | 상태 전이와 `events` 행을 한 트랜잭션에 기록 |
| Reconciler | 주기 실행. 워크플로 상태와 실제 Task 상태를 대조해 수렴 |

초기 요구 정의 지침대로 오케스트레이터는 LLM이 아니다. 작업 순서·반복 횟수·승인 조건 같은 확정적 규칙은
일반 프로그램 코드로 구현한다.

### 경계 검증

- 에이전트는 다른 에이전트를 모른다. 입력 Artifact만 받고 출력 Artifact만 낸다.
 → SP2에서 스텁을 LLM로 교체해도 오케스트레이터는 변경 없음.
- 오케스트레이터는 Task 내부 상태를 모른다. push로 받는 완료/실패 통지만 본다.
- Artifact 테이블은 오케스트레이터 소유이고 SDK의 Task 테이블과 스키마가 분리된다.
 → 아웃박스를 같은 트랜잭션에 묶을 수 있다.

## 6. 데이터 모델

SDK가 자체 Task 테이블(`tasks`, `push_notification_configs`)을 소유한다. `create_task_model(table_name, base)`에
커스텀 `DeclarativeBase`를 넘겨 SDK 테이블을 별도 스키마(`a2a`)에 배치하고, 아래 오케스트레이터
스키마(`public`)와 분리한다. 같은 DB·다른 스키마이므로 아웃박스를 한 트랜잭션에 묶을 수 있다.

아래는 오케스트레이터 소유 스키마다.

```sql
-- 요구사항 단위 워크플로 상태
CREATE TABLE workflow_requirements (
 requirement_id TEXT PRIMARY KEY, -- REQ-001
 title TEXT NOT NULL,
 state TEXT NOT NULL, -- planned|implementing|verifying|
 -- remediating|blocked|accepted|escalated
 revision INT NOT NULL DEFAULT 1,
 max_revisions INT NOT NULL DEFAULT 3,
 run_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 에이전트 1회 실행 = 1행. 불변.
CREATE TABLE workflow_tasks (
 task_id UUID PRIMARY KEY,
 requirement_id TEXT NOT NULL REFERENCES workflow_requirements,
 agent TEXT NOT NULL, -- planner|dev|qa|security
 revision INT NOT NULL, -- 소속 요구사항의 환류 회차.
 -- 같은 회차의 dev/qa/security가 동일 값을 공유한다.
 a2a_task_id TEXT, -- SDK가 발급
 idempotency_key TEXT NOT NULL,
 state TEXT NOT NULL, -- submitted|working|completed|failed|canceled
 verdict TEXT, -- PASS|FAIL (qa/security만)
 failure_class TEXT, -- transport|execution|poison
 attempt INT NOT NULL DEFAULT 1,
 revision_of UUID REFERENCES workflow_tasks,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 completed_at TIMESTAMPTZ,
 UNIQUE (idempotency_key)
);
CREATE INDEX ON workflow_tasks (requirement_id, revision);
CREATE INDEX ON workflow_tasks (state) WHERE state IN ('submitted','working');

CREATE TABLE artifacts (
 artifact_id UUID PRIMARY KEY,
 requirement_id TEXT NOT NULL REFERENCES workflow_requirements,
 producer_task UUID NOT NULL REFERENCES workflow_tasks,
 kind TEXT NOT NULL, -- requirements|source_code|
 -- test_report|security_report
 version INT NOT NULL,
 content JSONB NOT NULL,
 sha256 TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (requirement_id, kind, version)
);

-- 트랜잭셔널 아웃박스
CREATE TABLE events (
 event_id BIGSERIAL PRIMARY KEY,
 occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 aggregate TEXT NOT NULL, -- requirement|task
 aggregate_id TEXT NOT NULL,
 event_type TEXT NOT NULL,
 payload JSONB NOT NULL,
 published_at TIMESTAMPTZ -- NULL = 미발행
);
CREATE INDEX ON events (event_id) WHERE published_at IS NULL;

CREATE TABLE agent_registry (
 agent TEXT PRIMARY KEY,
 base_url TEXT NOT NULL,
 card JSONB NOT NULL,
 healthy BOOLEAN NOT NULL DEFAULT false,
 last_seen_at TIMESTAMPTZ
);
```

## 7. 상태 모델

**A2A Task 상태와 워크플로 상태를 분리한다.** 하나의 상태 기계로 합치려는 시도는 실패한다 —
A2A에서 종료 상태는 되돌릴 수 없으므로, `completed`된 dev Task를 QA 반려 때문에 다시
`working`으로 돌릴 방법이 없기 때문이다.

### 층 1 — A2A Task (SDK 소관, 에이전트마다)

```
submitted → working → completed | failed | canceled
 ↕
 input-required ← 사람 승인 대기 전용
```

`input-required`는 **재작업에 쓰지 않는다.** 초기 요구 정의 안전성 요구("배포, 결제, 개인정보 전송은
사람의 승인을 요구한다")가 A2A의 `input-required` 의미와 정확히 일치하므로 이 용도로 예약한다.

### 층 2 — 워크플로 (우리 소관, 요구사항마다)

```
planned → implementing → verifying ─(통과)→ accepted
 ↑ │
 │ (결함)
 │ ↓
 └── remediating ─(반복 상한 초과)→ escalated
 implementing ↔ blocked (승인 게이트)
```

### Task 불변성과 계보

시도마다 새 Task를 만들고 `revision_of`로 계보만 잇는다. 실패한 시도를 지우지 않는다.

```
REQ-001 회원가입 workflow: accepted · revisions=2
├─ task/dev/8f1a rev 1 → completed artifact: source_code@v1
├─ task/qa/3c02 → completed verdict: FAIL 중복 가입 미처리
├─ task/sec/9d77 → completed verdict: FAIL 평문 비밀번호 저장
├─ task/dev/b204 rev 2 revision_of: 8f1a → completed source_code@v2
├─ task/qa/f5e1 → completed verdict: PASS
└─ task/sec/1a93 → completed verdict: PASS
```

얻는 것:
- `failed`(인프라 장애 → 재시도)와 `verdict: FAIL`(품질 반려 → 환류)이 다른 층에 있어 섞이지 않는다.
- 수렴 횟수를 `COUNT(*)` 한 줄로 셀 수 있다 (SP3 비교실험).
- 시도마다 새 엣지가 그려져 환류가 그래프에 드러난다 (시각화 B).
- 반복 상한이 워크플로 층에 있어 에이전트가 우회할 수 없다.

감수하는 것:
- Task 행 증가 (수렴까지 요구사항당 6~10행). 인덱스로 대응.
- SP2에서 dev 에이전트가 이전 시도 맥락을 자동으로 갖지 못한다. 이전 코드와 리포트를
 입력 Artifact로 명시 전달해야 한다. 토큰이 더 들지만 사용량이 명시적으로 드러나 측정이 정직해진다.

## 8. 실패 처리

### 실패 5분류

| 종류 | 예 | 대응 | 층 |
|---|---|---|---|
| 전송 실패 | 도달 불가, 5xx, 커넥션 타임아웃 | 백오프 재시도 (최대 3회) | 인프라 |
| 실행 실패 | AgentExecutor 예외, OOM | 백오프 재시도 (최대 2회) | 인프라 |
| 독성 입력 | 스키마 위반, 깨진 Artifact 참조 | **재시도 금지**, 즉시 `failed` | 인프라 |
| 품질 반려 | `verdict: FAIL` | 환류 (revision + 1) | 워크플로 |
| 정책 차단 | 승인 거부, 반복 상한 초과 | `escalated` | 워크플로 |

위 셋은 같은 일을 다시 시도하고, 아래 둘은 다른 일을 시작한다.
독성 입력을 분리한 이유는 재시도해도 동일 실패가 확정적이라 백오프가 느린 실패가 될 뿐이기 때문이다.

### 멱등성

A2A Task가 `failed`면 종료 상태이므로 재시도는 새 Task 발행이다. 그 새 발행들이 서로 구분되고
계보가 남게 하는 것이 멱등성 키다.

```
idempotency_key = sha256(requirement_id | agent | revision | attempt)
```

필드 경계는 길이 접두사로 인코딩해 구분자 주입을 막는다. `attempt`는 1일 때 재료에서 빠지므로
첫 시도의 키는 이 필드가 생기기 전과 같은 값이다.

**이 키가 실제로 하는 일은 두 가지다.**

1. **`workflow_tasks`의 유니크 태그.** 열에 유니크 제약이 걸려 있어, 같은 (요구사항, 에이전트,
 회차, 시도)에 대해 행이 두 개 생기는 것을 DB가 막는다. 동시에 디스패치하는 두 경로는 둘 다
 `attempt = COUNT + 1`을 같은 값으로 계산하므로 같은 키를 만들고, 뒤늦은 쪽이 유니크 위반으로
 떨어진다.
2. **계보.** `revision`이 재료에 있으므로 환류로 인한 정당한 재실행은 자연히 다른 키가 되고,
 `attempt`가 있으므로 크래시한 행을 대체하는 재디스패치도 다른 키가 된다 — Task 행은 불변이라
 재시도가 기존 행을 고쳐 쓰는 대신 새 행을 만들어야 하는데, 회차만으로는 키가 충돌한다.

키는 A2A 메시지 메타데이터(`vsi_idempotency_key`)로 에이전트에게도 전달된다. 다만 **에이전트는
이 키로 중복을 제거하지 않는다.** SP1에서 그럴 필요가 없기 때문이다 — 아래 조건을 보라.

> **SP2 경고 — 이 조건이 깨지면 에이전트측 멱등성이 필수가 된다.**
>
> 위 설계가 성립하는 근거는 하나뿐이다: **같은 키가 에이전트에 두 번 도달하는 경로가 없다.**
> 그 자리 재시도(같은 행·같은 키로 다시 보내기)는 `retry.is_undelivered`가 참인 경우,
> 즉 `ConnectError`/`ConnectTimeout`처럼 **요청이 상대에게 닿지 않았음이 증명된** 실패로만
> 좁혀져 있다. 그 외의 모호한 실패(읽기 타임아웃, 5xx, `wait_for` 타임아웃)는 그 자리에서
> 다시 보내지 않고 행을 실패로 확정하며, 리컨실러가 **새 `attempt` = 새 키**로 재디스패치한다.
>
> **그 자리 재시도의 범위를 "증명된 미전달" 밖으로 넓히는 순간 이 근거가 무너지고, 에이전트가
> 키로 중복 실행을 걸러내는 일이 필수가 된다.** SP1에서 중복 디스패치의 비용은 스텁을 한 번 더
> 부르는 것(0원)이지만, SP2에서는 **중복 LLM 과금**이다. 재시도 정책을 손보는 사람은 이 문단을
> 먼저 읽어야 한다.

### 타임아웃 계층

| 계층 | 기본값 |
|---|---|
| MCP 도구 실행 (SP2) | 30초 |
| AgentExecutor 작업 | 5분 |
| 워크플로 단계 | 15분 |
| 실행(run) 전체 | 60분 |

**안쪽 < 바깥쪽 부등식을 설정 로딩 시점에 검증하고, 위반 시 기동을 실패시킨다.**
바깥이 먼저 터지면 안쪽 실패 원인을 잃고 트레이스에 원인 없는 취소만 남는다.

### 크래시 복구

**에이전트 사망** — SDK TaskStore가 PostgreSQL이므로 재시작해도 Task 상태가 남는다.
오케스트레이터는 push 타임아웃 시 `tasks/get`으로 조회해, 완료됐으면 결과를 수용하고
응답이 없으면 새 Task를 발행한다.

**오케스트레이터 사망 — 리컨실리에이션 루프.**
워크플로 상태가 PostgreSQL에 있으므로, 주기적으로(기본 10초) `implementing`/`verifying`에
머무는 요구사항을 스캔해 실제 Task 상태와 대조하고 차이를 메운다. 이벤트를 재생하는 것이 아니라
현재 상태를 관찰해 목표 상태로 수렴시키는 방식이며, 쿠버네티스 컨트롤러와 같은 패턴이다.
크래시 복구가 특수 경로가 아니라 평시에도 도는 루프이므로 테스트가 쉽다.

**게이트웨이 사망** — 아웃박스에 이벤트가 쌓이고, 재시작 시 마지막 `published_at` 이후부터
이어서 테일한다. UI만 잠시 멈추고 유실은 없다.

### verdict의 출처

초기 요구 정의 안전성 요구 — *"에이전트가 생성한 테스트 결과를 그대로 신뢰하지 않고 실제 실행 결과로
검증한다."*

**`verdict`는 LLM의 주장이 아니라 도구의 종료 코드에서 도출한다.** QA 에이전트가 통과를
주장해도 워크플로는 테스트 실행 결과만 본다. SP1에서는 스텁이 종료 코드를 흉내내고,
SP2에서 실제 pytest로 대체된다.

### 하지 않는 것

- 사가 보상 트랜잭션 없음 — Artifact는 추가만 되므로 롤백 대상이 없다.
- 에이전트 간 직접 재시도 없음 — 재시도 권한은 오케스트레이터만.
- 서킷 브레이커 없음 — 내부 서비스 4개, 재시도 상한으로 충분.

## 9. 테스트 전략

LLM을 제거하면 비결정성이 사라져 **전 구간 통합 테스트가 결정적으로 동작한다.**
이것이 SP1을 스텁으로 먼저 만드는 이유다.

| 층 | 대상 | 도구 |
|---|---|---|
| 단위 | 워크플로 상태 전이, 정책 게이트, 멱등성 키, 타임아웃 부등식 | pytest |
| 계약 | AgentCard 스키마, A2A 메시지·Artifact 스키마 | pytest + jsonschema |
| 통합 | 오케스트레이터 + 에이전트 4종 + PostgreSQL 실제 기동 | pytest + docker compose |
| 실패 주입 | 재시도, 멱등성, 리컨실리에이션 | 스텁 실패 모드 |
| 관측성 | span 계층, 아웃박스 이벤트 순서 | 인메모리 OTel exporter + DB 조회 |

### 스텁 에이전트는 실패 주입기다

고정 응답만 반환하는 스텁은 해피 패스만 검증한다. 시나리오를 입력받는 테스트 장비로 설계한다.

```yaml
scenario: qa_fails_twice
agents:
 qa: { verdicts: [FAIL, FAIL, PASS] }
 security: { verdicts: [FAIL, PASS, PASS] }
 dev: { attempt_2: crash, latency_ms: 200 }
```

지원 주입 모드:

| 모드 | 검증 대상 |
|---|---|
| verdict 시퀀스 | 환류 루프, 반복 상한 |
| 크래시 (프로세스 종료 / 500) | 전송·실행 실패 재시도 |
| 지연 | 타임아웃 계층이 안쪽부터 발동하는지 |
| 중복 호출 카운터 | 멱등성 위반 탐지 |
| 행(hang) | push 타임아웃 후 `tasks/get` 폴백 |

SP3 비교실험 하네스가 이 장비를 재사용한다.

### 리컨실리에이션 검증

```
1. 시나리오 시작 → 요구사항이 verifying 진입까지 대기
2. docker kill -s SIGKILL orchestrator
3. docker compose up -d orchestrator
4. 개입 없이 accepted 도달 확인
5. 최종 Artifact와 Task 계보가 kill 없이 실행한 결과와 동일한지 대조
```

kill 시점을 파라미터화해 여러 지점에 주입한다.
"실행 중 임의 시점에 kill -9 해도 최종 상태가 같다"가 이 프로젝트의 핵심 검증이다.

### CI

GitHub Actions. 단위·계약은 매 푸시, 통합·실패주입은 docker compose 기동 후 실행.
구현은 TDD로 진행하며 위 주입 시나리오가 명세 역할을 한다.

## 10. SP1 완료 정의

1. `docker compose up` 후 단일 명령으로 샘플 요구사항 1건이 **환류 2회를 거쳐 `accepted`까지** 자동 완주
2. Jaeger에서 **단일 트레이스**로 기획 → 개발 → QA/보안 → 개발 재시도 전 구간 확인
3. React 그래프 UI에서 Task 이동이 실시간 관찰됨
4. 오케스트레이터 SIGKILL 후 재기동해도 개입 없이 완주
5. **LLM 호출 0회, 토큰 비용 0원** — 전 과정이 무료로 재현 가능

5번이 SP1의 정체성이다. 인프라 완성을 토큰 없이 증명한다.

## 11. SDK 검증 결과 (2026-09-17 스파이크)

`a2a-sdk` 1.1.2를 실제 설치해 API 표면을 확인했다. 검증 코드는 폐기했다.

| 항목 | 결과 |
|---|---|
| PostgreSQL Task 지속 | `DatabaseTaskStore(engine: AsyncEngine, table_name, create_table, owner_resolver)` 존재. `a2a.migrations` + `a2a_db_cli`로 마이그레이션 제공 |
| 스키마 분리 | `create_task_model(table_name, base)`로 커스텀 `Base` 주입 가능 → 별도 스키마 배치 가능 |
| push notification | `ClientConfig.push_notification_config`로 클라이언트가 웹훅 등록. 에이전트는 `BasePushNotificationSender`가 `X-A2A-Notification-Token` 헤더를 붙여 POST |
| 푸시 설정 저장 | `DatabasePushNotificationConfigStore` — `encryption_key` 지원 |
| 폴백 | `ClientConfig.polling` 존재 → 푸시 실패 시 폴링 경로 확보 |
| OTel 전파 | **자동 아님.** `[telemetry]` extra는 `opentelemetry-api`/`-sdk`만 설치. 계측 패키지를 직접 추가해야 함 (3절 참조) |
| httpx 주입 | `ClientConfig.httpx_client`로 계측된 클라이언트 주입 가능 → 전파 경로 확보 |
| ASGI 앱 | `A2AFastAPIApplication` **없음**. `add_a2a_routes_to_fastapi()`로 우리 앱에 라우트 부착 |
| AgentCard 필드 | `name`, `description`, `supported_interfaces`, `provider`, `version`, `documentation_url`, `capabilities`, `security_schemes`, `security_requirements`, `default_input_modes`, `default_output_modes`, `skills`, `signatures`, `icon_url` |
| AgentCapabilities 필드 | `streaming`, `push_notifications`, `extensions`, `extended_agent_card` |

**보안 요구 충족 경로 확인.** 푸시 토큰은 `DatabasePushNotificationConfigStore`에 저장되고
AgentCard에는 포함되지 않는다. 초기 요구 정의 안전성 요구 *"API 키를 Agent Card나 메시지에 포함하지
않는다"* 를 SDK 구조가 이미 만족한다. SP1의 `security_schemes`는 내부 네트워크 전제의
공유 시크릿 방식으로 선언한다.

### 남은 조사 항목

구현 중 확인하며, 설계를 뒤집지 않는 범위다.

- `DatabaseTaskStore`의 `owner_resolver` 기본값(`resolve_user_scope`)이 단일 테넌트 환경에서
 어떻게 동작하는지 — 초기 요구 정의의 "작업 소유권" 요구와 연결 가능한지 검토
- `a2a_db_cli` 마이그레이션을 docker compose 기동 순서에 어떻게 끼울지
- `FastAPIInstrumentor`가 SDK가 추가한 라우트까지 계측하는지 (부착 순서 의존 가능성)

## 12. SP2로 넘기는 제약

SP1 구현이 끝나면서 **SP2가 깨뜨리기 쉬운** 전제들이 드러났다. 여기 모아 둔다.

### 12.1 그 자리 재시도를 넓히면 에이전트측 멱등성이 필수가 된다 (⚠ 과금 직결)

§8 멱등성의 경고 문단과 같은 내용이다. 두 번 적는 이유는, 이 제약을 깨뜨릴 사람이 스펙의
실패 처리 절이 아니라 **재시도 정책 코드**(`packages/orchestrator/retry.py`)를 보며 작업할
것이기 때문이다.

- **현재 성립하는 것**: 같은 멱등성 키가 에이전트에 두 번 도달하는 경로가 없다. 그 자리
 재시도는 `is_undelivered`(= `ConnectError`/`ConnectTimeout`, 바이트 하나도 나가지 않았음이
 증명된 실패)로만 좁혀져 있고, 모호한 실패는 행을 끝내고 새 `attempt`(= 새 키)로 다시 간다.
- **깨뜨리는 변경**: `is_undelivered`의 범위를 넓히는 것, 모호한 실패를 같은 행에서 다시
 보내도록 되돌리는 것, 또는 `attempt`를 키 재료에서 빼는 것.
- **깨뜨릴 때 반드시 함께 해야 하는 것**: 에이전트가 `vsi_idempotency_key`로 완료된 Task를
 찾아 재실행 대신 기존 Artifact를 반환하도록 구현하고, 그 동작을 테스트로 고정한다.
- **안 하면 치르는 비용**: SP1에서는 스텁을 한 번 더 부르는 것(0원). **SP2에서는 중복 LLM
 과금**이고, 증상이 조용하다 — 결과는 맞게 나오고 청구서만 늘어난다.

### 12.2 `run_s`는 아직 집행되지 않는다

타임아웃 계층의 `run_s`(60분)를 집행하는 코드는 SP1에 없다. SP1의 종료 보장은 시간 예산이
아니라 **리컨실러의 관측**(열린 행 나이 천장 `stuck_after_s`, 실패 행 수 재시도 캡)에 서
있다. SP2에서 실제 LLM 지연이 붙어 "느린 것"과 "멈춘 것"을 나이만으로 가르기 어려워지면
그때 `run_s`를 리컨실러의 요구사항 나이 backstop으로 배선한다. 자세한 것은
`packages/orchestrator/policy.py`의 모듈 docstring에 층별 배선 현황 표로 있다.

### 12.3 `stuck_after_s` 기본값 60초는 측정된 값이 아니다

SP1 스텁 기준의 추측값이다. `reconciler.force_fail` span의 `vsi.task.open_age_s` 분포가
쌓여야 근거를 갖고 조정할 수 있다. **SP2에서 실제 LLM 지연이 붙으면 정상 작업이 이 천장에
강제 실패당한다** — SP2 착수 시 가장 먼저 올려야 할 값이다.
