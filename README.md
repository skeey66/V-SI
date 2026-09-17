# V-SI — 가상 SI 회사

기획·개발·QA·보안 역할의 AI 에이전트가 A2A 프로토콜 위에서 협업해 소프트웨어를 만드는 시스템.

원 과제: 초기 요구 정의.
본 프로젝트는 그 초기 요구 정의를 참조 규격으로 삼되 **포트폴리오 목적**으로 범위를 재구성했다.

**증명 대상: 에이전트 인프라 · 분산 시스템 설계.**

## 현재 상태

| | 서브프로젝트 | 상태 |
|---|---|---|
| SP1 | A2A 커널 (스텁 에이전트, 오케스트레이션, 관측성, 크래시 복구) | 설계 완료 · 구현 전 |
| SP2 | MCP 도구 서버 + 실제 LLM 에이전트 4종 + 환류 루프 | 미착수 |
| SP3 | 대시보드 지표 + 단일 vs 다중 에이전트 비교 실험 | 미착수 |

## SP1이 증명하려는 것

LLM을 한 번도 호출하지 않고 오케스트레이션 전 구간이 동작하고, 관측 가능하며,
프로세스가 죽어도 복구된다. 완료 정의는 다섯 가지:

1. `docker compose up` 후 단일 명령으로 요구사항 1건이 환류 2회를 거쳐 `accepted`까지 자동 완주
2. Jaeger 단일 트레이스로 기획 → 개발 → QA/보안 → 개발 재시도 전 구간 확인
3. React 그래프 UI에서 Task 이동 실시간 관찰
4. 오케스트레이터 `SIGKILL` 후 재기동해도 개입 없이 완주
5. **LLM 호출 0회, 토큰 비용 0원**

## 문서

- [SP1 A2A 커널 설계](docs/superpowers/specs/2026-09-17-a2a-kernel-design.md)
- [설계 화면](docs/design-screens/) — 아키텍처·컴포넌트 경계·상태 모델 다이어그램 (브라우저에서 열람)

## 기술 스택

Python 3.11 · FastAPI · `a2a-sdk` 1.1.2 (A2A 스펙 1.0, HTTP+JSON) · PostgreSQL ·
OpenTelemetry (+ `instrumentation-fastapi` / `-httpx`) → Jaeger · React · pytest · Docker

SP2에서 추가: MCP Python SDK · Ruff · mypy · Bandit · Semgrep · Playwright
