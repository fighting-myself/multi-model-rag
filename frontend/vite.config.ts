import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 6006,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // 流式接口（如 /completions/stream）可能较久，避免 proxy 超时导致 socket hang up
        timeout: 300000, // 5 分钟
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            // 流式请求不设置超时，由后端控制
            const url = proxyReq.path || ''
            if (url.includes('/completions/stream')) {
              proxyReq.setTimeout(0)
              proxyReq.setNoDelay?.(true)
            }
          })
        },
      },
    },
  },
})
