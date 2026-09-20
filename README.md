# V-SI — 가상 SI 회사

기획·개발·QA·보안 역할의 AI 에이전트가 A2A 프로토콜 위에서 협업해 소프트웨어를 만드는 시스템.

소프트웨어 회사의 역할 분업을 그대로 프로세스 경계로 옮기면 어떤 인프라가 필요한지
직접 만들어 확인하는 프로젝트다.

**증명 대상: 에이전트 인프라 · 분산 시스템 설계.**

## 현재 상태

| | 서브프로젝트 | 상태 |
|---|---|---|
| SP1 | A2A 커널 (스텁 에이전트, 오케스트레이션, 관측성, 크래시 복구) | **구현 완료** · 완료 정의 5개 테스트로 고정 |
| SP2 | MCP 도구 서버 + 실제 LLM 에이전트 4종 + 환류 루프 | **구현 완료** · 완료 정의 6개 테스트로 고정 |
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

## SP2가 증명하려는 것

SP1의 커널 위에서, 스텁이 아니라 **로컬 모델(Ollama)이 실제로 도구를 호출해**
기획 → 개발 → QA/보안 → 환류를 완주한다. 비용은 여전히 0원이다. 완료 정의
6가지와, 각각을 무엇이 고정하는지는 [SP2 설계 문서](docs/specs/2026-09-18-sp2-llm-agents-design.md)
13절에 있다 — 핵심은 실제 LLM 종단 실행(기준 1), 워크스페이스 탈출 차단(기준 2),
판정은 도구 종료코드가 이긴다(기준 3), 환류가 실제 실패 출력에 근거한다(기준 4),
SP1 회귀 없음(기준 5), 외부 LLM API 호출 0회(기준 6)다. 6개 모두
`tests/integration/test_acceptance.py::test_completion_criterion_{1..6}_*`가 고정한다.

**실측 한 건**: `qa_fails_twice` 시나리오, 모델 `qwen3:8b`(호스트 Ollama), 비용 0원.
환류 2회(리비전 3회)를 거쳐 11분 30초 만에 `accepted`에 도달했다 — 리비전 1
`qa FAIL / security PASS`, 리비전 2 `qa FAIL / security PASS`, 리비전 3
`qa PASS / security PASS`. dev는 QA가 돌린 실제 pytest 실패
(`NameError: name 'add' is not defined`)를 읽고 고쳤다 — 판정도 피드백도
모델이 지어낸 게 아니라 도구 실행 결과다.

## 실행

```bash
cp .env.example .env          # VSI_PUSH_TOKEN (개발 전용 고정값)
docker compose up -d --build  # 9개 서비스 (기본값: 스텁 모드)
./scripts/demo.sh             # 스택 기동 + 요구사항 1건을 accepted까지 완주
```

기본 모드는 **스텁**이다 — `VSI_AGENT_MODE`를 주지 않으면 에이전트가 LLM을
한 번도 부르지 않고 1초 안에 끝난다. 실제 모델을 쓰려면 아래 "LLM 모드로 실행"을
본다 — 그 경우 요구사항 1건에 수 분이 걸린다(실측 11분 30초), 초 단위가 아니다.

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

## LLM 모드로 실행

로컬 Ollama가 필요하다. 비용은 0원이다.

```bash
ollama serve &
ollama pull qwen3:8b
VSI_AGENT_MODE=llm docker compose up -d --build
curl -X POST http://localhost:8000/requirements \
  -H 'Content-Type: application/json' \
  -d '{"requirement_id":"REQ-1","title":"정수 두 개를 더하는 add(a, b) 함수","run_id":"run-1"}'
```

요구사항 하나가 수 분 걸린다 — 실측 11분 30초(환류 2회, 리비전 3회). 스텁 모드의
1초와는 자릿수가 다르다. `http://localhost:5173`에서 에이전트가 어떤 도구를 쓰는지
실시간으로 보인다.

스텁 모드(기본값)는 `VSI_AGENT_MODE` 없이 그대로 쓴다 — 1초 만에 끝나고 LLM을
부르지 않는다.

## 테스트

파이썬과 웹이 나뉜다. 통합·수용 시험은 **스택이 떠 있어야** 한다.

```bash
pip install -e ".[dev]"
pytest tests                       # 단위 + 통합 + 수용 (스택 기동 필요, 약 4분)
pytest tests/orchestrator tests/agent_runtime tests/stub_agent  # 스택 없이 (postgres만 필요)
cd web && npm ci && npm test       # vitest — 그래프 리듀서
```

기본 실행에서는 `llm` 마커가 붙은 시험이 제외된다(`pyproject.toml`의
`addopts = "-m 'not llm'"`) — 실제 모델을 부르는 시험은 느리고 비결정적이라
CI 기본 스위트에 넣지 않는다. 실측 기본 스위트: **296 passed, 4 deselected**
(그 4개가 `llm`으로 마킹된 시험이다). `llm` 마커가 붙은 시험만 따로 돌리려면
`pytest tests -m llm --no-header`를 쓴다 — 로컬 Ollama가 떠 있어야 하고
요구사항 하나에 10분 이상 걸린다.

**`escalated`로 끝나는 것도 성공이다.** 8b 로컬 모델이 과제를 못 푸는 일은
흔하고, 그때 환류 상한이 제대로 돌아 실행이 유한하게 끝나는 것이 이 설계가
작동한다는 증거다. 시험은 "코드가 맞다"가 아니라 "종단 상태에 도달했다,
판정이 종료코드에서 나왔다, 도구가 실제로 호출됐다"를 단언한다.

`tests/orchestrator`는 "단위"지만 진짜 postgres에 붙는다(매 시험마다 스키마를 비운다).
CI는 세 job으로 나뉘어 있다 — `unit`(postgres 서비스 컨테이너), `integration`(컴포즈 전체
기동 후 `tests/integration tests/services`), `web`(vitest).

## 문서

- [SP1 A2A 커널 설계](docs/specs/2026-09-17-a2a-kernel-design.md)
- [SP2 LLM 에이전트 설계](docs/specs/2026-09-18-sp2-llm-agents-design.md)
- [설계 화면](docs/design-screens/) — 아키텍처·컴포넌트 경계·상태 모델 다이어그램 (브라우저에서 열람)

## 기술 스택

Python 3.11 · FastAPI · `a2a-sdk` 1.1.2 (A2A 스펙 1.0, HTTP+JSON) · PostgreSQL ·
OpenTelemetry (+ `instrumentation-fastapi` / `-httpx`) → Jaeger · React · pytest · Docker

SP2에서 추가: MCP Python SDK (`mcp` 2.x) · Ollama (`qwen3:8b`) · Bandit · pytest

`pytest` 와 `bandit` 은 개발 의존성이 아니라 **워크스페이스 컨테이너의 런타임
의존성**이다 — QA 와 보안 에이전트가 생성된 코드에 대고 실제로 실행하는 도구다.
