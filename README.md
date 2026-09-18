# V-SI — 가상 SI 회사

기획·개발·QA·보안 역할의 AI 에이전트가 A2A 프로토콜 위에서 협업해 소프트웨어를 만드는 시스템.

소프트웨어 회사의 역할 분업을 그대로 프로세스 경계로 옮기면 어떤 인프라가 필요한지
직접 만들어 확인하는 프로젝트다.

**증명 대상: 에이전트 인프라 · 분산 시스템 설계.**

## 현재 상태

| | 서브프로젝트 | 상태 |
|---|---|---|
| SP1 | A2A 커널 (스텁 에이전트, 오케스트레이션, 관측성, 크래시 복구) | **구현 완료** · 완료 정의 5개 테스트로 고정 |
| SP2 | MCP 도구 서버 + 실제 LLM 에이전트 4종 + 환류 루프 | 미착수 |
| SP3 | 대시보드 지표 + 단일 vs 다중 에이전트 비교 실험 | 미착수 |

## SP1이 증명하려는 것

LLM을 한 번도 호출하지 않고 오케스트레이션 전 구간이 동작하고, 관측 가능하며,
프로세스가 죽어도 복구된다. 완료 정의 다섯 가지와, 각각을 무엇이 고정하는지:

| | 완료 정의 | 고정하는 시험 |
|---|---|---|
| 1 | `docker compose up` 후 단일 명령으로 요구사항 1건이 환류 2회를 거쳐 `accepted`까지 자동 완주 | `test_acceptance.py::..._criterion_1_*` |
| 2 | Jaeger 단일 트레이스로 기획 → 개발 → QA/보안 → 개발 재시도 전 구간 확인 | `..._criterion_2_*` (Jaeger API로 5개 서비스가 한 트레이스인지 확인) |
| 3 | React 그래프 UI에서 Task 이동 실시간 관찰 | `..._criterion_3_*` — **주의: WebSocket 층에서만 단언한다** |
| 4 | 오케스트레이터 `SIGKILL` 후 재기동해도 개입 없이 완주 | `..._criterion_4_*` (실제로 컨테이너를 SIGKILL한다) |
| 5 | **LLM 호출 0회, 토큰 비용 0원** | `..._criterion_5_*` (배포되는 소스와 `package.json` 스캔) |

**3번은 정직하게 말한다.** 이 리포에는 브라우저를 구동해 렌더링 결과를 확인하는
배선(Playwright·Chrome MCP 등)이 없다. 시험이 증명하는 것은 "React 그래프가 실제로
구독하는 바로 그 WebSocket(`ws://localhost:8100/ws`)이 네 에이전트의 Task 이동을
워크플로 진행에 맞춰 시간차를 두고 전달한다"까지다. React 쪽 소비 로직은
`web/src/useEventStream.test.ts`(vitest)가 리듀서 단위로 따로 고정한다. 둘을 합쳐도
"사람 눈에 보이는 애니메이션"은 보증하지 않는다.

## 실행

```bash
cp .env.example .env          # VSI_PUSH_TOKEN (개발 전용 고정값)
docker compose up -d --build  # 9개 서비스
./scripts/demo.sh             # 스택 기동 + 요구사항 1건을 accepted까지 완주
```

`scripts/demo.sh`는 `qa_fails_twice` 시나리오로 `REQ-DEMO` 하나를 돌린다 — qa가 두 번,
security가 한 번 FAIL한 뒤 통과하므로 **환류 2회**를 거쳐 revision 3에서 `accepted`가
된다. 중간에 dev가 한 번 크래시하도록 주입돼 있어 리컨실러의 복구 경로도 함께 돈다.

| 창구 | 주소 |
|---|---|
| 그래프 UI (React) | http://localhost:5173 |
| 오케스트레이터 API | http://localhost:8000 (`POST /requirements`, `GET /requirements/{id}`, `GET /healthz`) |
| 이벤트 게이트웨이 | http://localhost:8100 — WebSocket은 `ws://localhost:8100/ws` |
| Jaeger UI | http://localhost:16686 |
| PostgreSQL | `postgresql://vsi:vsi@localhost:55432/vsi` |

에이전트 4종은 `8001`(planner) `8002`(dev) `8003`(qa) `8004`(security)로 각각 열려 있다 —
A2A 카드는 `/.well-known/agent-card.json`에서 직접 볼 수 있다.

요구사항을 손으로 하나 넣으려면:

```bash
curl -X POST localhost:8000/requirements -H 'content-type: application/json' \
  -d '{"requirement_id":"REQ-001","title":"회원가입","run_id":"run-1"}'
curl localhost:8000/requirements/REQ-001
```

시나리오를 바꾸려면 `VSI_SCENARIO`를 주고 에이전트를 재생성한다(시나리오는 에이전트
프로세스 안의 상태 기계라 재기동해야 바뀐다):

```bash
VSI_SCENARIO=scenarios/all_pass.yaml docker compose up -d --force-recreate \
  --no-deps planner dev qa security
```

## 테스트

파이썬과 웹이 나뉜다. 통합·수용 시험은 **스택이 떠 있어야** 한다.

```bash
pip install -e ".[dev]"
pytest tests                       # 단위 + 통합 + 수용 (스택 기동 필요, 약 4분)
pytest tests/orchestrator tests/agent_runtime tests/stub_agent  # 스택 없이 (postgres만 필요)
cd web && npm ci && npm test       # vitest — 그래프 리듀서
```

`tests/orchestrator`는 "단위"지만 진짜 postgres에 붙는다(매 시험마다 스키마를 비운다).
CI는 세 job으로 나뉘어 있다 — `unit`(postgres 서비스 컨테이너), `integration`(컴포즈 전체
기동 후 `tests/integration tests/services`), `web`(vitest).

## 문서

- [SP1 A2A 커널 설계](docs/specs/2026-09-17-a2a-kernel-design.md)
- [설계 화면](docs/design-screens/) — 아키텍처·컴포넌트 경계·상태 모델 다이어그램 (브라우저에서 열람)

## 기술 스택

Python 3.11 · FastAPI · `a2a-sdk` 1.1.2 (A2A 스펙 1.0, HTTP+JSON) · PostgreSQL ·
OpenTelemetry (+ `instrumentation-fastapi` / `-httpx`) → Jaeger · React · pytest · Docker

SP2에서 추가: MCP Python SDK · Ruff · mypy · Bandit · Semgrep · Playwright
