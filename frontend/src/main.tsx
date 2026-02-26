import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './index.css'

const theme = {
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
    Card: {
      headerBg: 'transparent',
    },
    Button: {
      primaryShadow: '0 2px 8px rgba(6, 182, 212, 0.25)',
      defaultShadow: '0 1px 2px rgba(15, 23, 42, 0.05)',
    },
    Menu: {
      itemBorderRadius: 8,
      itemMarginBlock: 2,
      itemPaddingInline: 12,
    },
  },
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
