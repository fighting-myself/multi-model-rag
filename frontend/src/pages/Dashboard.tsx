import { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic, Typography } from 'antd'
import { FileOutlined, DatabaseOutlined, MessageOutlined, LinkOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import api from '../services/api'
import type { DashboardStats, UsageLimitsResponse } from '../types/api'

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
    <div>
      <h1 className="app-page-title">仪表盘</h1>
      <p className="app-page-desc">概览您的文件、知识库与对话用量</p>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={24} md={8}>
          <Card loading={loading} style={{ borderLeft: '4px solid var(--app-accent)' }}>
            <Statistic
              title="文件总数"
              value={stats.file_count}
              prefix={<FileOutlined style={{ color: 'var(--app-accent)' }} />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={24} md={8}>
          <Card loading={loading} style={{ borderLeft: '4px solid #10b981' }}>
            <Statistic
              title="知识库数量"
              value={stats.knowledge_base_count}
              prefix={<DatabaseOutlined style={{ color: '#10b981' }} />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={24} md={8}>
          <Card loading={loading} style={{ borderLeft: '4px solid #8b5cf6' }}>
            <Statistic
              title="对话次数"
              value={stats.conversation_count}
              prefix={<MessageOutlined style={{ color: '#8b5cf6' }} />}
            />
          </Card>
        </Col>
      </Row>
      {usageLimits && (
        <Card title="当日用量与限流" loading={loading}>
          <Typography.Text type="secondary">
            上传 {usageLimits.upload_today}/{usageLimits.upload_limit_per_day} 次
            · 对话 {usageLimits.conversation_today}/{usageLimits.conversation_limit_per_day} 条
            · 检索 QPS 上限 {usageLimits.search_qps_limit}/秒
          </Typography.Text>
          <Typography.Text style={{ marginLeft: 8 }}>
            <Link to="/billing"><LinkOutlined /> 计费中心</Link>
          </Typography.Text>
        </Card>
      )}
    </div>
  )
}
