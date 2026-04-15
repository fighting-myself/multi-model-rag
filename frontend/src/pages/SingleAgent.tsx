import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Collapse, Input, List, Segmented, Space, Spin, Tag, message } from 'antd'
import { ApiOutlined, ToolOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'

import api, { consumeSingleAgentRunStream } from '../services/api'
import type { AgentToolItem, SingleAgentRunRequest, SingleAgentSsePayload } from '../types/api'

const { TextArea } = Input
const THINKING_PREVIEW_LINES = 5

function lastNLines(text: string, n: number): string {
  const lines = text.split('\n')
  return lines.slice(-n).join('\n')
}

function traceToText(trace: Array<{ step?: string; title?: string; text?: string; data?: unknown }>): string {
  return trace
    .map((t) => `${t.title || t.step || '步骤'}: ${t.text || ''}`.trim())
    .filter(Boolean)
    .join('\n')
}

export default function SingleAgent() {
  const navigate = useNavigate()
  const { paradigm: paradigmFromRoute } = useParams()
  const normalizedParadigm = useMemo(() => {
    const p = (paradigmFromRoute || '').toLowerCase()
    if (p === 'react' || p === 'plan_execute' || p === 'reflexion' || p === 'rewoo') {
      return p as SingleAgentRunRequest['paradigm']
    }
    return 'plan_execute' as SingleAgentRunRequest['paradigm']
  }, [paradigmFromRoute])

  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [tools, setTools] = useState<AgentToolItem[]>([])
  const [result, setResult] = useState<Extract<SingleAgentSsePayload, { type: 'done' }> | null>(null)
  const [liveTrace, setLiveTrace] = useState<Array<{ step?: string; title?: string; text?: string; data?: unknown }>>([])
  const [paradigm, setParadigm] = useState<SingleAgentRunRequest['paradigm']>(normalizedParadigm)
  const [thinkingExpanded, setThinkingExpanded] = useState(false)

  const thinkingPreviewText = useMemo(
    () => lastNLines(traceToText(liveTrace), THINKING_PREVIEW_LINES),
    [liveTrace]
  )

  useEffect(() => {
    setParadigm(normalizedParadigm)
  }, [normalizedParadigm])

  const loadTools = async () => {
    try {
      const data = await api.get<AgentToolItem[]>('/single-agent/tools')
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
      await api.post('/single-agent/tools/seed')
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
    setLiveTrace([])
    try {
      await consumeSingleAgentRunStream(
        { query: q, paradigm },
        {
          onEvent: (evt) => {
            if (evt.type === 'trace') {
              setLiveTrace((prev) => [...prev, evt.item])
              return
            }
            if (evt.type === 'done') {
              setResult(evt)
              setLiveTrace(evt.trace || [])
              return
            }
            if (evt.type === 'error') {
              message.error(evt.detail || '执行失败')
            }
          },
        }
      )
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
            <span>单智能体</span>
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
          <Segmented
            value={paradigm}
            onChange={(v) => {
              const next = String(v) as SingleAgentRunRequest['paradigm']
              setParadigm(next)
              navigate(`/single-agent/${next}`)
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
            运行单智能体
          </Button>
          <Collapse
            size="small"
            items={[
              {
                key: 'tools',
                label: `已注册工具 (${tools.length})`,
                children: (
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
                ),
              },
            ]}
          />
        </Space>
      </Card>

      {loading && (
        <Card style={{ marginTop: 16 }}>
          <Spin tip="单智能体执行中..." />
        </Card>
      )}

      {(loading || liveTrace.length > 0) && (
        <Card
          title="思考区"
          style={{ marginTop: 16 }}
          extra={
            <Button type="link" size="small" onClick={() => setThinkingExpanded((v) => !v)}>
              {thinkingExpanded ? '收起' : '展开'}
            </Button>
          }
        >
          {loading && liveTrace.length === 0 && (
            <div style={{ marginBottom: 12 }}>
              <Spin size="small" /> <span style={{ marginLeft: 8 }}>正在生成思考步骤...</span>
            </div>
          )}
          {thinkingExpanded ? (
            <List
              bordered
              dataSource={liveTrace}
              locale={{ emptyText: loading ? '等待步骤...' : '暂无思考步骤' }}
              renderItem={(t) => (
                <List.Item>
                  <div>
                    <div style={{ fontWeight: 600 }}>{t.title || t.step || '步骤'}</div>
                    <div style={{ whiteSpace: 'pre-wrap', color: 'var(--app-text-secondary)' }}>{t.text || ''}</div>
                  </div>
                </List.Item>
              )}
            />
          ) : (
            <div
              style={{
                lineHeight: 1.6,
                fontFamily: 'ui-monospace, monospace',
                fontSize: 13,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                minHeight: `${THINKING_PREVIEW_LINES * 1.6}em`,
              }}
            >
              {thinkingPreviewText || (loading ? '等待步骤...' : '暂无思考步骤')}
            </div>
          )}
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
        </Card>
      )}
    </div>
  )
}
