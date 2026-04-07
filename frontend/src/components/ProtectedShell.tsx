import { useEffect, useState } from 'react'
import { Navigate, Outlet } from 'react-router-dom'
import { Spin } from 'antd'
import { useAuthStore } from '../stores/authStore'
import AppLayout from './AppLayout'

/**
 * 1. 等待 zustand persist 从 localStorage 恢复，避免首屏 isAuthenticated 误判导致路由乱跳。
 * 2. 未登录跳转登录页。
 * 3. 通过 AppLayout 渲染子路由（Outlet 作为 children 传入布局）。
 */
export default function ProtectedShell() {
  const { isAuthenticated } = useAuthStore()
  const [hydrated, setHydrated] = useState(() => useAuthStore.persist.hasHydrated())

  useEffect(() => {
    return useAuthStore.persist.onFinishHydration(() => setHydrated(true))
  }, [])

  if (!hydrated) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--app-bg-layout, #060a12)',
        }}
      >
        <Spin size="large" tip="加载中…" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return (
    <AppLayout>
      <Outlet />
    </AppLayout>
  )
}
