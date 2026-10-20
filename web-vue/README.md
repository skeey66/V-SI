# V-SI Vue 프론트엔드 (web-vue)

LangGraph 백엔드(`lg_api`)를 위한 Vue 3 화면입니다. API 계약은 `../docs/lg/api-contract.md` 를 따릅니다.

## 실행

```bash
npm install && npm run dev     # http://localhost:5180
npm run build                  # dist/ 생성
npx vitest run                 # 단위 테스트 (SSE 파서, 파이프라인 상태)
```

- 백엔드 `lg_api` 가 `http://localhost:8200` 에서 실행 중이어야 합니다.
- 개발 서버는 `/api/*` 요청을 8200 으로 프록시합니다 (`vite.config.js`). SSE 가 버퍼링되지 않도록 압축을 끄고 그대로 흘려보냅니다.
- 5180 포트가 이미 쓰이고 있으면 Vite 가 다음 포트(5175…)를 사용하니 터미널에 표시된 주소를 확인하세요.

## 구조

```
src/
  main.js, router.js, App.vue   앱 시작, 라우팅(/, /requirements/:id, /admin), 상단 메뉴
  style.css                     공통 스타일 (CSS 변수)
  api/
    client.js                   REST API fetch 래퍼
    sse.js                      자체 SSE 파서 + 재연결 스트림
    sse.test.js                 파서 단위 테스트
  utils/
    format.js                   상태 라벨, 시간/비용 포맷
    pipeline.js(.test.js)       이벤트 → 파이프라인 노드 상태 계산
  components/
    StatusBadge.vue             상태 배지
    PipelineStrip.vue           기획 → 게이트 → 개발 → (QA ∥ 보안) → 판정
    EventTimeline.vue           실시간 이벤트 타임라인
    AgentOutput.vue             에이전트별 토큰 스트리밍 출력
    ApprovalBox.vue             사람 승인(interrupt) 상자
    FileViewer.vue              결과물 파일 보기
    FeedbackBox.vue             👍/👎 피드백
  views/
    HomeView.vue                새 요청 + 요청 목록
    RequirementView.vue         실행 상세 (메인 데모 화면)
    AdminView.vue               관리자: 설정, 통계, 실행 목록, 프롬프트 버전
```

## SSE 파싱 방식

브라우저 `EventSource` 대신 `fetch` + `ReadableStream` 으로 직접 읽습니다 (`src/api/sse.js`).

1. `fetch()` 응답의 `res.body.getReader()` 로 바이트 조각을 받고, `TextDecoder(..., { stream: true })` 로 문자열로 바꿉니다. (한글이 조각 경계에서 잘려도 안전)
2. `createSseParser` 가 문자열을 버퍼에 이어 붙이고 줄 단위로 자릅니다. 마지막 미완성 줄은 다음 조각을 기다립니다.
3. 줄 처리 규칙
   - `event: 이름` → 이벤트 종류 저장
   - `data: 내용` → 데이터 줄 저장 (여러 줄이면 `\n` 으로 합침)
   - `: ping` 처럼 `:` 로 시작 → 주석이므로 무시
   - 빈 줄 → 이벤트 하나 완성, 콜백 호출
4. `openEventStream` 은 data 의 `seq` 를 기억해 두었다가 연결이 끊기면 2초 뒤 `?after=<마지막 seq>` 로 다시 연결합니다. `done`/`error` 를 받으면 재연결하지 않고, 화면을 떠나면 `AbortController` 로 연결을 끊습니다.
