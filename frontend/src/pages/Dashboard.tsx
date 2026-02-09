import { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic } from 'antd'
import { FileOutlined, DatabaseOutlined, MessageOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { DashboardStats } from '../types/api'

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats>({ file_count: 0, knowledge_base_count: 0, conversation_count: 0 })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get<DashboardStats>('/dashboard/stats')
      .then((data: DashboardStats) => setStats(data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>仪表盘</h1>
      <Row gutter={16}>
        <Col span={8}>
          <Card loading={loading}>
            <Statistic
              title="文件总数"
              value={stats.file_count}
              prefix={<FileOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card loading={loading}>
            <Statistic
              title="知识库数量"
              value={stats.knowledge_base_count}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card loading={loading}>
            <Statistic
              title="对话次数"
              value={stats.conversation_count}
              prefix={<MessageOutlined />}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
