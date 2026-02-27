import { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic, Typography } from 'antd'
import { FileOutlined, DatabaseOutlined, MessageOutlined, LinkOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import api from '../services/api'
import type { DashboardStats, UsageLimitsResponse } from '../types/api'

const accentColors = {
  cyan: '#00f5ff',
  green: '#22c55e',
  purple: '#a855f7',
} as const

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats>({ file_count: 0, knowledge_base_count: 0, conversation_count: 0 })
  const [usageLimits, setUsageLimits] = useState<UsageLimitsResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get<DashboardStats>('/dashboard/stats').catch(() => ({ file_count: 0, knowledge_base_count: 0, conversation_count: 0 })),
      api.get<UsageLimitsResponse>('/billing/usage-limits').catch(() => null),
    ])
      .then(([s, ul]) => {
        setStats(s)
        setUsageLimits(ul ?? null)
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="app-perspective">
      <h1 className="app-page-title app-animate-in">仪表盘</h1>
      <p className="app-page-desc app-animate-in app-animate-in-delay-1">概览您的文件、知识库与对话用量</p>
      <Row gutter={[20, 20]} style={{ marginBottom: 28 }}>
        <Col xs={24} sm={24} md={8} className="app-animate-in app-animate-in-delay-1">
          <Card
            loading={loading}
            className="app-card-3d"
            style={{
              borderLeft: `4px solid ${accentColors.cyan}`,
              boxShadow: `inset 4px 0 20px -4px ${accentColors.cyan}40`,
            }}
          >
            <Statistic
              title={<span style={{ color: 'var(--app-text-secondary)' }}>文件总数</span>}
              value={stats.file_count}
              valueStyle={{ fontFamily: "'JetBrains Mono', 'Orbitron', monospace", fontWeight: 700, color: accentColors.cyan, textShadow: `0 0 20px ${accentColors.cyan}80` }}
              prefix={<FileOutlined style={{ color: accentColors.cyan, marginRight: 8 }} />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={24} md={8} className="app-animate-in app-animate-in-delay-2">
          <Card
            loading={loading}
            className="app-card-3d"
            style={{
              borderLeft: `4px solid ${accentColors.green}`,
              boxShadow: `inset 4px 0 20px -4px ${accentColors.green}40`,
            }}
          >
            <Statistic
              title={<span style={{ color: 'var(--app-text-secondary)' }}>知识库数量</span>}
              value={stats.knowledge_base_count}
              valueStyle={{ fontFamily: "'JetBrains Mono', 'Orbitron', monospace", fontWeight: 700, color: accentColors.green, textShadow: `0 0 20px ${accentColors.green}80` }}
              prefix={<DatabaseOutlined style={{ color: accentColors.green, marginRight: 8 }} />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={24} md={8} className="app-animate-in app-animate-in-delay-3">
          <Card
            loading={loading}
            className="app-card-3d"
            style={{
              borderLeft: `4px solid ${accentColors.purple}`,
              boxShadow: `inset 4px 0 20px -4px ${accentColors.purple}40`,
            }}
          >
            <Statistic
              title={<span style={{ color: 'var(--app-text-secondary)' }}>对话次数</span>}
              value={stats.conversation_count}
              valueStyle={{ fontFamily: "'JetBrains Mono', 'Orbitron', monospace", fontWeight: 700, color: accentColors.purple, textShadow: `0 0 20px ${accentColors.purple}80` }}
              prefix={<MessageOutlined style={{ color: accentColors.purple, marginRight: 8 }} />}
            />
          </Card>
        </Col>
      </Row>
      {usageLimits && (
        <Card
          title={<span style={{ color: 'var(--app-accent)', fontFamily: "'Orbitron', sans-serif", fontWeight: 600 }}>当日用量与限流</span>}
          loading={loading}
          className="app-card-3d app-animate-in app-animate-in-delay-4"
          style={{ borderTop: '1px solid var(--app-glass-border)' }}
        >
          <Typography.Text style={{ color: 'var(--app-text-secondary)' }}>
            上传 {usageLimits.upload_today}/{usageLimits.upload_limit_per_day} 次
            · 对话 {usageLimits.conversation_today}/{usageLimits.conversation_limit_per_day} 条
            · 检索 QPS 上限 {usageLimits.search_qps_limit}/秒
          </Typography.Text>
          <Typography.Text style={{ marginLeft: 8 }}>
            <Link to="/billing" style={{ color: 'var(--app-accent)' }}><LinkOutlined /> 计费中心</Link>
          </Typography.Text>
        </Card>
      )}
    </div>
  )
}
