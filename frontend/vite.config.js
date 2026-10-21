import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5180,
    proxy: {
      // /api 로 시작하는 요청은 LangGraph 백엔드(lg_api)로 넘긴다.
      '/api': {
        // 도커에서는 VSI_API_URL=http://backend:8000 으로 바꿔 띄운다.
        target: process.env.VSI_API_URL || 'http://127.0.0.1:8200',
        changeOrigin: true,
        // SSE 응답이 버퍼링되지 않도록: 압축을 요청하지 않고, 응답을 그대로 흘려보낸다.
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('accept-encoding', 'identity')
          })
          proxy.on('proxyRes', (proxyRes) => {
            if ((proxyRes.headers['content-type'] || '').includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
  test: {
    environment: 'node',
  },
})
