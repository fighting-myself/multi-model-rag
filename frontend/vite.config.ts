import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const proxyTarget = process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 监听 0.0.0.0，便于用局域网 IP 打开前端；仅 localhost 时代理仍转发到本机 127.0.0.1:8000
    host: true,
    // 远程开发域名白名单（Linux/云主机转发访问）
    allowedHosts: ['u786977-9a5b-b4c69fe0.westc.seetacloud.com'],
    port: 6006,
    proxy: {
      '/api': {
        // Windows 下 localhost 可能解析到 IPv6 ::1，与仅监听 IPv4 的代理/服务组合时易异常；固定 127.0.0.1
        target: proxyTarget,
        changeOrigin: true,
        // 流式接口（如 /completions/stream）可能较久，避免 proxy 超时导致 socket hang up
        timeout: 300000, // 5 分钟
        configure: (proxy) => {
          proxy.on('error', (err) => {
            console.error(`[vite proxy] /api -> ${proxyTarget} 失败（请先启动后端）:`, err.message)
          })
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
