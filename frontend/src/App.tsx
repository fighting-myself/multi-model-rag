import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
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
import ExternalConnections from './pages/ExternalConnections'
import Steward from './pages/Steward'
import ComputerSteward from './pages/ComputerSteward'
import RecallEvaluation from './pages/RecallEvaluation'
import AdvancedRAGMetrics from './pages/AdvancedRAGMetrics'
import SingleAgent from './pages/SingleAgent'
import MultiAgent from './pages/MultiAgent'
import ProtectedShell from './components/ProtectedShell'
import { ErrorBoundary } from './components/ErrorBoundary'
import PageTitle from './components/PageTitle'

export default function App() {
  return (
    <BrowserRouter>
      <PageTitle />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route element={<ProtectedShell />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/files" element={<Files />} />
          <Route
            path="/knowledge-bases"
            element={
              <ErrorBoundary>
                <KnowledgeBases />
              </ErrorBoundary>
            }
          />
          <Route
            path="/chat"
            element={
              <ErrorBoundary>
                <Chat />
              </ErrorBoundary>
            }
          />
          <Route path="/billing" element={<Billing />} />
          <Route path="/profile" element={<Profile />} />
          <Route
            path="/image-search"
            element={
              <ErrorBoundary>
                <ImageSearch />
              </ErrorBoundary>
            }
          />
          <Route
            path="/recall-evaluation"
            element={
              <ErrorBoundary>
                <RecallEvaluation />
              </ErrorBoundary>
            }
          />
          <Route
            path="/advanced-rag-metrics"
            element={
              <ErrorBoundary>
                <AdvancedRAGMetrics />
              </ErrorBoundary>
            }
          />
          <Route path="/audit-log" element={<AuditLog />} />
          <Route
            path="/external-connections"
            element={
              <ErrorBoundary>
                <ExternalConnections />
              </ErrorBoundary>
            }
          />
          <Route path="/mcp-servers" element={<McpServers />} />
          <Route
            path="/steward"
            element={
              <ErrorBoundary>
                <Steward />
              </ErrorBoundary>
            }
          />
          <Route
            path="/computer-steward"
            element={
              <ErrorBoundary>
                <ComputerSteward />
              </ErrorBoundary>
            }
          />
          <Route
            path="/single-agent"
            element={
              <ErrorBoundary>
                <SingleAgent />
              </ErrorBoundary>
            }
          />
          <Route
            path="/single-agent/:paradigm"
            element={
              <ErrorBoundary>
                <SingleAgent />
              </ErrorBoundary>
            }
          />
          <Route
            path="/multi-agent"
            element={
              <ErrorBoundary>
                <MultiAgent />
              </ErrorBoundary>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
