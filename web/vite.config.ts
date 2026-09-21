import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // 승인 버튼(`POST /api/requirements/{id}/approve`)이 지나갈 길.
    //
    // 브라우저에서 `http://localhost:8000` 을 직접 부르면 출처가 달라 CORS 에
    // 막힌다. 오케스트레이터에 CORS 를 여는 대신 여기서 프록시하는 이유는
    // **노출을 넓히지 않기 위해서다**: `POST /requirements` 는 아직 인증이
    // 없고(스펙 §14.3 이 남겨 둔 구멍), CORS 를 열면 사용자가 방문한 아무
    // 페이지나 이 스택에 요구사항을 밀어 넣을 수 있게 된다. 프록시는 그
    // 경로를 이 개발 서버 안에만 둔다.
    //
    // 대상이 `localhost` 가 아니라 `orchestrator` 인 것은 이 Vite 가 컴포즈
    // 네트워크 **안에서** 돌기 때문이다(web 서비스). 브라우저가 아니라
    // 컨테이너가 나가는 요청이라 도커 내부 이름이 맞다.
    //
    // WebSocket(게이트웨이)은 그대로 `ws://localhost:8100/ws` 로 브라우저가
    // 직접 붙는다 — 핸드셰이크는 CORS 대상이 아니라 막히지 않는다.
    proxy: {
      "/api": {
        target: "http://orchestrator:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "node",
  },
});
