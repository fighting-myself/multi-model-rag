import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Input, List, Segmented, Space, Spin, Tag, message } from 'antd'
import { ApiOutlined, ToolOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'

import api from '../services/api'
import type { AgentToolItem, MultiAgentRunRequest, MultiAgentRunResponse } from '../types/api'

const { TextArea } = Input

export default function MultiAgent() {
  const navigate = useNavigate()
  const { paradigm: paradigmFromRoute } = useParams()
  const normalizedParadigm = useMemo(() => {
    const p = (paradigmFromRoute || '').toLowerCase()
    if (p === 'react' || p === 'plan_execute' || p === 'reflexion' || p === 'rewoo') {
      return p as MultiAgentRunRequest['paradigm']
    }
    return 'plan_execute' as MultiAgentRunRequest['paradigm']
  }, [paradigmFromRoute])

  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [tools, setTools] = useState<AgentToolItem[]>([])
  const [result, setResult] = useState<MultiAgentRunResponse | null>(null)
  const [paradigm, setParadigm] = useState<MultiAgentRunRequest['paradigm']>(normalizedParadigm)

  useEffect(() => {
    setParadigm(normalizedParadigm)
  }, [normalizedParadigm])

  const loadTools = async () => {
    try {
      const data = await api.get<AgentToolItem[]>('/multi-agent/tools')
      setTools(data || [])
    } catch (e: unknown) {
      message.error((e as Error)?.message || '加载工具失败')
    }
  }

  useEffect(() => {
    loadTools().catch(() => {})
  }, [])

  const seedTools = async () => {
    setSeeding(true)
    try {
      await api.post('/multi-agent/tools/seed')
      message.success('已写入默认工具')
      await loadTools()
    } catch (e: unknown) {
      message.error((e as Error)?.message || '写入默认工具失败')
    } finally {
      setSeeding(false)
    }
  }

  const run = async () => {
    const q = query.trim()
    if (!q) {
      message.warning('请输入问题')
      return
    }
    setLoading(true)
    setResult(null)
    try {
      const data = await api.post<MultiAgentRunResponse>('/multi-agent/run', { query: q, paradigm }, { timeout: 180000 })
      setResult(data)
    } catch (e: unknown) {
      message.error((e as Error)?.message || '执行失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <Card
        title={
          <Space>
            <ApiOutlined />
            <span>多智能体</span>
          </Space>
        }
        extra={
          <Space>
            <Button onClick={loadTools}>刷新工具</Button>
            <Button loading={seeding} onClick={seedTools}>
              初始化默认工具
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="支持 4 种范式：ReAct / Plan & Execute / Reflexion / ReWOO"
            description="工具来自数据库，可通过“初始化默认工具”快速写入网页搜索、天气、金融行情。"
          />
          <Segmented
            value={paradigm}
            onChange={(v) => {
              const next = String(v) as MultiAgentRunRequest['paradigm']
              setParadigm(next)
              navigate(`/multi-agent/${next}`)
            }}
            options={[
              { label: 'ReAct', value: 'react' },
              { label: 'Plan & Execute', value: 'plan_execute' },
              { label: 'Reflexion', value: 'reflexion' },
              { label: 'ReWOO', value: 'rewoo' },
            ]}
          />
          <TextArea
            rows={4}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例如：先查今天上海天气，再结合最近公开新闻判断对出行和消费股可能有什么影响"
          />
          <Button type="primary" onClick={run} loading={loading}>
            运行多智能体
          </Button>
        </Space>
      </Card>

      <Card title="已注册工具" style={{ marginTop: 16 }}>
        <List
          dataSource={tools}
          locale={{ emptyText: '暂无工具，请先初始化默认工具' }}
          renderItem={(it) => (
            <List.Item>
              <List.Item.Meta
                avatar={<ToolOutlined />}
                title={
                  <Space>
                    <span>{it.name}</span>
                    <Tag>{it.code}</Tag>
                    <Tag color={it.enabled ? 'green' : 'default'}>{it.enabled ? '启用' : '禁用'}</Tag>
                  </Space>
                }
                description={it.description || '-'}
              />
            </List.Item>
          )}
        />
      </Card>

      {loading && (
        <Card style={{ marginTop: 16 }}>
          <Spin tip="多智能体执行中..." />
        </Card>
      )}

      {!loading && result && (
        <Card title="执行结果" style={{ marginTop: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <strong>范式：</strong>
            <Tag color="blue" style={{ marginLeft: 8 }}>{result.paradigm}</Tag>
          </div>
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.75 }}>{result.answer}</div>
          {result.tools_used?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong>工具调用：</strong>
              <Space wrap style={{ marginLeft: 8 }}>
                {result.tools_used.map((x) => (
                  <Tag key={x}>{x}</Tag>
                ))}
              </Space>
            </div>
          )}
          {result.trace?.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <strong>执行轨迹</strong>
              <List
                style={{ marginTop: 8 }}
                bordered
                dataSource={result.trace}
                renderItem={(t) => (
                  <List.Item>
                    <div>
                      <div style={{ fontWeight: 600 }}>{t.title || t.step || '步骤'}</div>
                      <div style={{ whiteSpace: 'pre-wrap', color: 'var(--app-text-secondary)' }}>{t.text || ''}</div>
                    </div>
                  </List.Item>
                )}
              />
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
