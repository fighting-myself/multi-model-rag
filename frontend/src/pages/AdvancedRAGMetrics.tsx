import { useState, useEffect } from 'react'
import {
  Card,
  Typography,
  Button,
  Space,
  Tag,
  message,
  Alert,
  Row,
  Col,
  Statistic,
  Select,
  Collapse,
  Table,
} from 'antd'
import { ThunderboltOutlined, LinkOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import api from '../services/api'
import PageSkeleton from '../components/PageSkeleton'
import type {
  RAGMetricsResponse,
  RAGMetricItem,
  KnowledgeBaseListResponse,
  KnowledgeBaseItem,
} from '../types/api'

const { Title, Paragraph, Text } = Typography

type MetricId = 'accuracy' | 'recall' | 'precision' | 'latency' | 'hallucination' | 'qps'

interface AccuracyDetail {
  query: string
  expected: string
  answer: string
  score: number
}
interface RecallDetail {
  query: string
  retrieved_ids: number[]
  relevant_ids: number[]
  recall_at_k: Record<number, number>
  hit_at_k?: Record<number, number>
  mrr?: number
}
interface HallucinationDetail {
  query: string
  answer_snippet: string
  score: number
  is_likely_hallucination: boolean
}

interface LastResult {
  accuracy?: { accuracy_pct: number; num_queries: number; details?: AccuracyDetail[] }
  recall?: { metrics: Record<string, number>; details?: RecallDetail[] }
  precision?: {
    metrics: Record<string, number>
    precision_at_k?: Record<number, number>
    details?: RecallDetail[]
  }
  latency?: {
    ttft_ms_avg: number | null
    e2e_ms_avg: number | null
    samples: number
    ttft_ms_samples?: number[]
    e2e_ms_samples?: number[]
  }
  hallucination?: { hallucination_rate_pct: number; num_queries: number; details?: HallucinationDetail[] }
  qps?: {
    qps: number
    avg_latency_ms: number | null
    failure_rate_pct: number
    total_requests?: number
    success?: number
    elapsed_sec?: number
  }
}

export default function AdvancedRAGMetrics() {
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState<RAGMetricsResponse | null>(null)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [selectedKbId, setSelectedKbId] = useState<number | null>(null)
  const [runningMetric, setRunningMetric] = useState<MetricId | null>(null)
  const [lastResult, setLastResult] = useState<LastResult>({})
  const navigate = useNavigate()

  useEffect(() => {
    const fetch = async () => {
      try {
        const [metricsRes, kbRes] = await Promise.all([
          api.get<RAGMetricsResponse>('/evaluation/rag-metrics'),
          api.get<KnowledgeBaseListResponse>('/knowledge-bases'),
        ])
        setData(metricsRes)
        setKnowledgeBases(kbRes.knowledge_bases || [])
        if ((kbRes.knowledge_bases?.length ?? 0) > 0 && !selectedKbId) {
          setSelectedKbId(kbRes.knowledge_bases![0].id)
        }
      } catch {
        message.error('获取 RAG 指标或知识库列表失败')
      } finally {
        setLoading(false)
      }
    }
    fetch()
  }, [])

  const runMetric = async (metricId: MetricId) => {
    setRunningMetric(metricId)
    try {
      if (metricId === 'accuracy') {
        const res = await api.post<LastResult['accuracy']>('/evaluation/rag-metrics/run-accuracy', {
          knowledge_base_id: selectedKbId ?? undefined,
          knowledge_base_ids: selectedKbId ? [selectedKbId] : undefined,
        }, { timeout: 300000 })
        setLastResult((r) => ({ ...r, accuracy: res }))
        message.success(`准确率评测完成：${res.accuracy_pct}%`)
      } else if (metricId === 'recall') {
        if (!selectedKbId) {
          message.warning('请先选择知识库')
          return
        }
        const res = await api.post<LastResult['recall']>('/evaluation/rag-metrics/run-recall', {
          knowledge_base_id: selectedKbId,
        }, { timeout: 300000 })
        setLastResult((r) => ({ ...r, recall: res }))
        message.success('召回率评测完成')
      } else if (metricId === 'precision') {
        if (!selectedKbId) {
          message.warning('请先选择知识库')
          return
        }
        const res = await api.post<LastResult['precision']>('/evaluation/rag-metrics/run-precision', {
          knowledge_base_id: selectedKbId,
        }, { timeout: 300000 })
        setLastResult((r) => ({ ...r, precision: res }))
        message.success('精准度评测完成')
      } else if (metricId === 'latency') {
        const res = await api.post<LastResult['latency']>('/evaluation/rag-metrics/run-latency', {
          num_samples: 3,
        }, { timeout: 120000 })
        setLastResult((r) => ({ ...r, latency: res }))
        message.success('延迟评测完成')
      } else if (metricId === 'hallucination') {
        const res = await api.post<LastResult['hallucination']>('/evaluation/rag-metrics/run-hallucination', {
          knowledge_base_id: selectedKbId ?? undefined,
          knowledge_base_ids: selectedKbId ? [selectedKbId] : undefined,
        }, { timeout: 300000 })
        setLastResult((r) => ({ ...r, hallucination: res }))
        message.success(`幻觉率评测完成：${res.hallucination_rate_pct}%`)
      } else if (metricId === 'qps') {
        const res = await api.post<LastResult['qps']>('/evaluation/rag-metrics/run-qps', {
          concurrency: 5,
          requests_per_worker: 2,
        }, { timeout: 120000 })
        setLastResult((r) => ({ ...r, qps: res }))
        message.success('QPS 评测完成')
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : '评测失败')
    } finally {
      setRunningMetric(null)
    }
  }

  if (loading) return <PageSkeleton />

  const metrics = data?.metrics ?? []
  const latencyStandards = data?.latency_standards ?? {}

  return (
    <div className="app-page-container" style={{ padding: 24 }}>
      <Title level={3} className="app-page-title app-animate-in" style={{ marginBottom: 8 }}>
        RAG 六大指标
      </Title>
      <Paragraph type="secondary" className="app-animate-in" style={{ marginBottom: 16 }}>
        按优先级排列，支持一键评测。默认评测集已内置（首次访问时自动生成并保存）。
      </Paragraph>

      <Card size="small" className="app-card-3d app-animate-in" style={{ marginBottom: 24 }}>
        <Space align="center">
          <Text>评测用知识库（召回/精准必选，准确率/幻觉可选）：</Text>
          <Select
            placeholder="选择知识库"
            value={selectedKbId ?? undefined}
            onChange={(v) => setSelectedKbId(v ?? null)}
            style={{ minWidth: 200 }}
            allowClear
            options={knowledgeBases.map((kb) => ({ label: kb.name, value: kb.id }))}
          />
          <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => navigate('/recall-evaluation')}>
            召回率评测页
          </Button>
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        {metrics.map((m: RAGMetricItem) => {
          const isRunning = runningMetric === m.id
          const needKb = m.id === 'recall' || m.id === 'precision'
          const result = lastResult[m.id as keyof LastResult]
          return (
            <Col xs={24} lg={12} xl={8} key={m.id}>
              <Card
                className="app-card-3d app-animate-in"
                size="small"
                title={
                  <Space>
                    <Tag color="blue">{m.priority}</Tag>
                    <span>{m.name}</span>
                    {m.name_en && (
                      <Text type="secondary" style={{ fontSize: 12 }}>{m.name_en}</Text>
                    )}
                  </Space>
                }
                extra={
                  <Button
                    type="primary"
                    size="small"
                    icon={<ThunderboltOutlined />}
                    loading={isRunning}
                    disabled={needKb && !selectedKbId}
                    onClick={() => runMetric(m.id as MetricId)}
                  >
                    一键评测
                  </Button>
                }
                style={{ height: '100%' }}
              >
                <Paragraph type="secondary" style={{ marginBottom: 8, fontSize: 13 }}>
                  {m.description}
                </Paragraph>
                {m.tip && (
                  <Alert
                    message={m.tip}
                    type="info"
                    showIcon
                    style={{ marginBottom: 12, fontSize: 12 }}
                  />
                )}
                {/* 结果展示 */}
                {m.id === 'accuracy' && result && 'accuracy_pct' in result && (
                  <div style={{ marginTop: 12 }}>
                    <Statistic title="答案准确率" value={(result as LastResult['accuracy'])!.accuracy_pct} suffix="%" />
                    <Text type="secondary"> 样本数：{(result as LastResult['accuracy'])!.num_queries}</Text>
                    {(result as LastResult['accuracy'])!.details?.length ? (
                      <Collapse
                        style={{ marginTop: 8 }}
                        items={[{
                          key: '1',
                          label: '评测明细（逐条）',
                          children: (
                            <Table
                              size="small"
                              pagination={false}
                              dataSource={(result as LastResult['accuracy'])!.details}
                              rowKey={(_, i) => String(i)}
                              columns={[
                                { title: '问题', dataIndex: 'query', width: 120, ellipsis: true, render: (t: string) => t?.slice(0, 30) + (t?.length > 30 ? '…' : '') },
                                { title: '期望答案', dataIndex: 'expected', width: 140, ellipsis: true },
                                { title: '模型回答', dataIndex: 'answer', ellipsis: true, render: (t: string) => t?.slice(0, 80) + (t?.length > 80 ? '…' : '') },
                                { title: '得分', dataIndex: 'score', width: 64, render: (s: number) => (s * 100).toFixed(0) + '%' },
                              ]}
                            />
                          ),
                        }]}
                      />
                    ) : null}
                  </div>
                )}
                {m.id === 'recall' && result && 'metrics' in result && (
                  <div style={{ marginTop: 12 }}>
                    <Row gutter={8}>
                      {[1, 3, 5, 10].map((k) => (
                        <Col span={6} key={k}>
                          <Statistic
                            title={`Recall@${k}`}
                            value={((result as LastResult['recall'])!.metrics[`recall_at_${k}`] ?? 0) * 100}
                            precision={1}
                            suffix="%"
                          />
                        </Col>
                      ))}
                    </Row>
                    <Text type="secondary">MRR: {((result as LastResult['recall'])!.metrics?.mrr ?? 0).toFixed(3)}</Text>
                    {(result as LastResult['recall'])!.details?.length ? (
                      <Collapse
                        style={{ marginTop: 8 }}
                        items={[{
                          key: '1',
                          label: '评测明细（每条查询的检索结果）',
                          children: (
                            <Table
                              size="small"
                              pagination={false}
                              dataSource={(result as LastResult['recall'])!.details}
                              rowKey={(_, i) => String(i)}
                              columns={[
                                { title: '问题', dataIndex: 'query', width: 100, ellipsis: true },
                                { title: '检索到ID(前5)', dataIndex: 'retrieved_ids', width: 120, render: (ids: number[]) => (ids?.slice(0, 5) || []).join(', ') },
                                { title: '标准答案ID', dataIndex: 'relevant_ids', width: 100, render: (ids: number[]) => (ids || []).join(', ') },
                                { title: 'Recall@5', key: 'r5', width: 72, render: (_: unknown, row: RecallDetail) => ((row.recall_at_k?.[5] ?? 0) * 100).toFixed(0) + '%' },
                                { title: 'MRR', dataIndex: 'mrr', width: 56, render: (v: number) => (v ?? 0).toFixed(2) },
                              ]}
                            />
                          ),
                        }]}
                      />
                    ) : null}
                  </div>
                )}
                {m.id === 'precision' && result && 'precision_at_k' in result && (
                  <div style={{ marginTop: 12 }}>
                    <Row gutter={8}>
                      {[1, 3, 5, 10].map((k) => (
                        <Col span={6} key={k}>
                          <Statistic
                            title={`P@${k}`}
                            value={((result as LastResult['precision'])!.precision_at_k?.[k] ?? 0) * 100}
                            precision={1}
                            suffix="%"
                          />
                        </Col>
                      ))}
                    </Row>
                    {(result as LastResult['precision'])!.details?.length ? (
                      <Collapse
                        style={{ marginTop: 8 }}
                        items={[{
                          key: '1',
                          label: '评测明细（每条查询的检索与精准度）',
                          children: (
                            <Table
                              size="small"
                              pagination={false}
                              dataSource={(result as LastResult['precision'])!.details}
                              rowKey={(_, i) => String(i)}
                              columns={[
                                { title: '问题', dataIndex: 'query', width: 100, ellipsis: true },
                                { title: '检索到ID(前5)', dataIndex: 'retrieved_ids', width: 120, render: (ids: number[]) => (ids?.slice(0, 5) || []).join(', ') },
                                { title: '标准答案ID', dataIndex: 'relevant_ids', width: 90, render: (ids: number[]) => (ids || []).join(', ') },
                                { title: 'Recall@5', key: 'r5', width: 72, render: (_: unknown, row: RecallDetail) => ((row.recall_at_k?.[5] ?? 0) * 100).toFixed(0) + '%' },
                              ]}
                            />
                          ),
                        }]}
                      />
                    ) : null}
                  </div>
                )}
                {m.id === 'latency' && result && 'ttft_ms_avg' in result && (
                  <div style={{ marginTop: 12 }}>
                    <Row gutter={16}>
                      <Col span={12}>
                        <Statistic
                          title="TTFT 平均"
                          value={(result as LastResult['latency'])!.ttft_ms_avg ?? '-'}
                          suffix="ms"
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic
                          title="E2E 平均"
                          value={(result as LastResult['latency'])!.e2e_ms_avg ?? '-'}
                          suffix="ms"
                        />
                      </Col>
                    </Row>
                    <Text type="secondary"> 样本数：{(result as LastResult['latency'])!.samples}</Text>
                    {((result as LastResult['latency'])!.ttft_ms_samples?.length ?? 0) > 0 && (
                      <Collapse
                        style={{ marginTop: 8 }}
                        items={[{
                          key: '1',
                          label: '各次采样',
                          children: (
                            <div style={{ fontSize: 13 }}>
                              {((result as LastResult['latency'])!.ttft_ms_samples ?? []).map((ttft, i) => (
                                <div key={i}>
                                  第 {i + 1} 次：TTFT <Text code>{(ttft ?? 0).toFixed(0)} ms</Text>
                                  ，E2E <Text code>{((result as LastResult['latency'])!.e2e_ms_samples?.[i] ?? 0).toFixed(0)} ms</Text>
                                </div>
                              ))}
                            </div>
                          ),
                        }]}
                      />
                    )}
                    <div style={{ marginTop: 8, fontSize: 12, color: '#666' }}>
                      {Object.entries(latencyStandards).map(([k, v]) => (
                        <div key={k}>{v}</div>
                      ))}
                    </div>
                  </div>
                )}
                {m.id === 'hallucination' && result && 'hallucination_rate_pct' in result && (
                  <div style={{ marginTop: 12 }}>
                    <Statistic
                      title="幻觉率"
                      value={(result as LastResult['hallucination'])!.hallucination_rate_pct}
                      suffix="%"
                    />
                    <Text type="secondary"> 样本数：{(result as LastResult['hallucination'])!.num_queries}</Text>
                    {(result as LastResult['hallucination'])!.details?.length ? (
                      <Collapse
                        style={{ marginTop: 8 }}
                        items={[{
                          key: '1',
                          label: '评测明细（逐条）',
                          children: (
                            <Table
                              size="small"
                              pagination={false}
                              dataSource={(result as LastResult['hallucination'])!.details}
                              rowKey={(_, i) => String(i)}
                              columns={[
                                { title: '问题', dataIndex: 'query', width: 100, ellipsis: true },
                                { title: '回答摘要', dataIndex: 'answer_snippet', ellipsis: true },
                                { title: '得分', dataIndex: 'score', width: 56, render: (s: number) => (s * 100).toFixed(0) + '%' },
                                { title: '疑似幻觉', dataIndex: 'is_likely_hallucination', width: 72, render: (v: boolean) => v ? <Tag color="red">是</Tag> : <Tag color="green">否</Tag> },
                              ]}
                            />
                          ),
                        }]}
                      />
                    ) : null}
                  </div>
                )}
                {m.id === 'qps' && result && 'qps' in result && (
                  <div style={{ marginTop: 12 }}>
                    <Row gutter={8}>
                      <Col span={8}>
                        <Statistic title="QPS" value={(result as LastResult['qps'])!.qps} />
                      </Col>
                      <Col span={8}>
                        <Statistic
                          title="平均延迟"
                          value={(result as LastResult['qps'])!.avg_latency_ms ?? '-'}
                          suffix="ms"
                        />
                      </Col>
                      <Col span={8}>
                        <Statistic
                          title="失败率"
                          value={(result as LastResult['qps'])!.failure_rate_pct}
                          suffix="%"
                        />
                      </Col>
                    </Row>
                    {((result as LastResult['qps'])!.total_requests != null || (result as LastResult['qps'])!.elapsed_sec != null) && (
                      <Collapse
                        style={{ marginTop: 8 }}
                        items={[{
                          key: '1',
                          label: '评测过程',
                          children: (
                            <div style={{ fontSize: 13 }}>
                              <p>总请求数：{(result as LastResult['qps'])!.total_requests ?? '-'}</p>
                              <p>成功：{(result as LastResult['qps'])!.success ?? '-'}</p>
                              <p>总耗时：{(result as LastResult['qps'])!.elapsed_sec ?? '-'} 秒</p>
                            </div>
                          ),
                        }]}
                      />
                    )}
                  </div>
                )}
                {!result && !isRunning && (
                  <div style={{ marginTop: 12 }}>
                    <Text type="secondary">点击「一键评测」使用默认评测集运行。</Text>
                    {needKb && <div><Text type="warning">召回/精准需先选择上方知识库。</Text></div>}
                  </div>
                )}
              </Card>
            </Col>
          )
        })}
      </Row>

      <Card className="app-card-3d app-animate-in" style={{ marginTop: 24 }} title="企业标准速查">
        <Row gutter={[24, 16]}>
          <Col span={24}>
            <Title level={5}>延迟（2026 通用）</Title>
            <ul>
              <li>内网/内部工具：≤ 1~2 秒</li>
              <li>ToC 产品/对话助手：≤ 800ms~1s</li>
              <li>搜索类：≤ 500ms</li>
              <li>召回、准确率再高，慢到 3s+ 直接不能上线</li>
            </ul>
          </Col>
          <Col span={24}>
            <Title level={5}>幻觉率</Title>
            <p>金融、法律、医疗：必须 &lt; 1%。</p>
          </Col>
        </Row>
      </Card>
    </div>
  )
}
