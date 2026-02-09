import { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic, Table, Button, message } from 'antd'
import { DollarOutlined, FileOutlined, DatabaseOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { UsageResponse, PlanListResponse } from '../types/api'

export default function Billing() {
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [plans, setPlans] = useState<PlanListResponse['plans']>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get<UsageResponse>('/billing/usage').catch(() => null),
      api.get<PlanListResponse>('/billing/plans').catch(() => ({ plans: [], total: 0 })),
    ])
      .then(([u, p]) => {
        if (u) setUsage(u)
        if (p?.plans) setPlans(p.plans)
      })
      .finally(() => setLoading(false))
  }, [])

  const handleSubscribe = async (planId: number) => {
    try {
      await api.post('/billing/subscribe', { plan_id: planId, payment_method: 'alipay' })
      message.success('订阅成功')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '订阅失败')
    }
  }

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>计费中心</h1>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card loading={loading}>
            <Statistic
              title="本月上传次数"
              value={usage?.file_uploads ?? 0}
              prefix={<FileOutlined />}
              suffix="次"
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card loading={loading}>
            <Statistic
              title="存储空间"
              value={usage?.storage_mb ?? 0}
              prefix={<DatabaseOutlined />}
              suffix="MB"
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card loading={loading}>
            <Statistic
              title="本月费用"
              value={usage?.cost ?? 0}
              prefix={<DollarOutlined />}
              suffix="元"
            />
          </Card>
        </Col>
      </Row>
      <Card title="套餐列表" loading={loading}>
        <Table
          rowKey="id"
          columns={[
            { title: '套餐名称', dataIndex: 'name', key: 'name' },
            { title: '描述', dataIndex: 'description', key: 'description' },
            { title: '价格（元）', dataIndex: 'price', key: 'price', render: (v: number | string) => (typeof v === 'number' ? v.toFixed(2) : v) },
            {
              title: '操作',
              key: 'action',
              render: (_: any, record: any) => (
                <Button type="primary" onClick={() => handleSubscribe(record.id)}>订阅</Button>
              ),
            },
          ]}
          dataSource={plans}
          pagination={false}
        />
      </Card>
    </div>
  )
}
