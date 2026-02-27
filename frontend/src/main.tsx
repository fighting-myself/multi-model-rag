import React, { useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { useThemeStore } from './stores/themeStore'
import './index.css'

// 首屏前恢复主题，避免闪烁（默认科技风深色）
try {
  const raw = localStorage.getItem('app-theme')
  if (raw) {
    const parsed = JSON.parse(raw) as { state?: { theme?: string } }
    const t = parsed?.state?.theme ?? 'dark'
    document.documentElement.setAttribute('data-theme', t)
  } else {
    document.documentElement.setAttribute('data-theme', 'dark')
  }
} catch {
  document.documentElement.setAttribute('data-theme', 'dark')
}

/* 科技风：霓虹青为主色，深色基底 */
const tokenBase = {
  colorPrimary: '#00f5ff',
  colorSuccess: '#22c55e',
  colorWarning: '#f59e0b',
  colorError: '#ef4444',
  colorInfo: '#00f5ff',
  borderRadius: 10,
  fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
}

/* 亮色主题：日间风格，深青主色，白/浅灰背景 */
const themeLight = {
  token: {
    ...tokenBase,
    colorPrimary: '#0d9488',
    colorInfo: '#0d9488',
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f0f4f8',
    colorText: '#0f172a',
    colorTextSecondary: '#475569',
  },
  components: {
    Card: { headerBg: 'transparent' },
    Button: {
      primaryShadow: '0 4px 14px -2px rgba(13, 148, 136, 0.35)',
      defaultShadow: '0 2px 8px rgba(15, 23, 42, 0.06)',
    },
    Menu: { itemBorderRadius: 10, itemMarginBlock: 4, itemPaddingInline: 12 },
  },
}

const themeDark = {
  token: {
    ...tokenBase,
    colorBgContainer: 'rgba(13, 18, 32, 0.85)',
    colorBgLayout: '#050810',
    colorBgElevated: '#1e293b',
    colorText: '#e2e8f0',
    colorTextSecondary: '#94a3b8',
  },
  components: themeLight.components,
}

function ThemeWrapper({ children }: { children: React.ReactNode }) {
  const theme = useThemeStore((s: ThemeState) => s.theme)
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])
  return (
    <ConfigProvider locale={zhCN} theme={theme === 'dark' ? themeDark : themeLight}>
      {children}
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeWrapper>
      <App />
    </ThemeWrapper>
  </React.StrictMode>,
)
