import { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic, Table, Button, message, Progress } from 'antd'
import { DollarOutlined, FileOutlined, DatabaseOutlined, MessageOutlined, SearchOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { UsageResponse, PlanListResponse, UsageLimitsResponse } from '../types/api'

export default function Billing() {
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [usageLimits, setUsageLimits] = useState<UsageLimitsResponse | null>(null)
  const [plans, setPlans] = useState<PlanListResponse['plans']>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get<UsageResponse>('/billing/usage').catch(() => null),
      api.get<UsageLimitsResponse>('/billing/usage-limits').catch(() => null),
      api.get<PlanListResponse>('/billing/plans').catch(() => ({ plans: [], total: 0 })),
    ])
      .then(([u, ul, p]) => {
        if (u) setUsage(u)
        if (ul) setUsageLimits(ul)
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
      <h1 className="app-page-title">计费中心</h1>
      <p className="app-page-desc">用量统计与套餐订阅</p>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
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

      {usageLimits && (
        <Card title="用量与限流" style={{ marginBottom: 24 }}>
          <Row gutter={24}>
            <Col span={8}>
              <div style={{ marginBottom: 8 }}>
                <span><FileOutlined /> 当日上传</span>
                <span style={{ marginLeft: 8, fontWeight: 600 }}>{usageLimits.upload_today} / {usageLimits.upload_limit_per_day}</span>
              </div>
              <Progress percent={usageLimits.upload_limit_per_day ? Math.min(100, (usageLimits.upload_today / usageLimits.upload_limit_per_day) * 100) : 0} size="small" />
            </Col>
            <Col span={8}>
              <div style={{ marginBottom: 8 }}>
                <span><MessageOutlined /> 当日对话</span>
                <span style={{ marginLeft: 8, fontWeight: 600 }}>{usageLimits.conversation_today} / {usageLimits.conversation_limit_per_day}</span>
              </div>
              <Progress percent={usageLimits.conversation_limit_per_day ? Math.min(100, (usageLimits.conversation_today / usageLimits.conversation_limit_per_day) * 100) : 0} size="small" />
            </Col>
            <Col span={8}>
              <div style={{ marginBottom: 8 }}>
                <span><SearchOutlined /> 检索 QPS 上限</span>
                <span style={{ marginLeft: 8, fontWeight: 600 }}>{usageLimits.search_qps_limit}/秒</span>
              </div>
              <div style={{ fontSize: 12, color: '#666' }}>当前秒请求数：{usageLimits.search_current_second}</div>
            </Col>
          </Row>
        </Card>
      )}

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
