import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from 'antd'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import Files from './pages/Files'
import KnowledgeBases from './pages/KnowledgeBases'
import Chat from './pages/Chat'
import Billing from './pages/Billing'
import Profile from './pages/Profile'
import ImageSearch from './pages/ImageSearch'
import AuditLog from './pages/AuditLog'
import McpServers from './pages/McpServers'
import Steward from './pages/Steward'
import AppLayout from './components/AppLayout'
import { ErrorBoundary } from './components/ErrorBoundary'
import PageTitle from './components/PageTitle'
import { useAuthStore } from './stores/authStore'

const { Content } = Layout

function App() {
  const { isAuthenticated } = useAuthStore()

  return (
    <BrowserRouter>
      <PageTitle />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route
          path="/*"
          element={
            isAuthenticated ? (
              <AppLayout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/files" element={<Files />} />
                  <Route path="/knowledge-bases" element={<ErrorBoundary><KnowledgeBases /></ErrorBoundary>} />
                  <Route path="/chat" element={<ErrorBoundary><Chat /></ErrorBoundary>} />
                  <Route path="/billing" element={<Billing />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route path="/image-search" element={<ErrorBoundary><ImageSearch /></ErrorBoundary>} />
                  <Route path="/audit-log" element={<AuditLog />} />
                  <Route path="/mcp-servers" element={<McpServers />} />
                  <Route path="/steward" element={<ErrorBoundary><Steward /></ErrorBoundary>} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </AppLayout>
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App
