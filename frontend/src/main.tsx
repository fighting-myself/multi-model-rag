import React, { useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { useThemeStore } from './stores/themeStore'
import './index.css'

// 首屏前恢复主题，避免闪烁
try {
  const raw = localStorage.getItem('app-theme')
  if (raw) {
    const parsed = JSON.parse(raw) as { state?: { theme?: string } }
    if (parsed?.state?.theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark')
    }
  }
} catch {
  // ignore
}

const themeLight = {
  token: {
    colorPrimary: '#06b6d4',
    colorSuccess: '#10b981',
    colorWarning: '#f59e0b',
    colorError: '#ef4444',
    colorInfo: '#06b6d4',
    borderRadius: 8,
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f1f5f9',
    colorText: '#0f172a',
    colorTextSecondary: '#64748b',
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  },
  components: {
    Card: { headerBg: 'transparent' },
    Button: {
      primaryShadow: '0 2px 8px rgba(6, 182, 212, 0.25)',
      defaultShadow: '0 1px 2px rgba(15, 23, 42, 0.05)',
    },
    Menu: { itemBorderRadius: 8, itemMarginBlock: 2, itemPaddingInline: 12 },
  },
}

const themeDark = {
  token: {
    ...themeLight.token,
    colorBgContainer: '#1e293b',
    colorBgLayout: '#0f172a',
    colorText: '#f1f5f9',
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
