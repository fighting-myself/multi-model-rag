import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Input, List, Select, Space, Spin, Tag, message } from 'antd'
import { ClusterOutlined } from '@ant-design/icons'

import { consumeMultiAgentRunStream } from '../services/api'
import type {
  MultiAgentRunRequest,
  MultiAgentTraceItem,
} from '../types/api'

const { TextArea } = Input

const SCENES: Array<{ value: MultiAgentRunRequest['scene']; label: string; desc: string }> = [
  {
    value: 'finance_research',
    label: '金融 / 投研',
    desc: '研究员(ReAct) -> 基本面(Plan&Execute) -> 技术面(ReAct) -> 风险(ReWOO) -> 投研总监(Reflection) -> 报告',
  },
  {
    value: 'market_ops',
    label: '市场运营 / 增长',
    desc: '洞察-策略-执行-复盘，适合活动与增长决策',
  },
  {
    value: 'compliance_risk',
    label: '法务合规 / 风险控制',
    desc: '条款审查、政策核验、风险分级与整改建议',
  },
  {
    value: 'product_strategy',
    label: '产品策略 / 规划',
    desc: '需求研究、竞品分析、路线图与里程碑',
  },
]

const COLLAPSED_LINE_COUNT = 5
const LINE_HEIGHT_EM = 1.5

function isMetaTrace(step?: string): boolean {
  return step === 'scene' || step === 'paradigm' || step === 'params'
}

function traceBlockText(t: MultiAgentTraceItem): string {
  const title = t.title || t.step || '步骤'
  const phase = t.phase || ''
  const thinking = (t.thinking || '').trim()
  const raw = (t.output ?? t.text ?? '').trim()
  const oneLine = raw.replace(/\s+/g, ' ').slice(0, 400)
  const resultLabel = isMetaTrace(t.step) ? '说明' : '结果'
  return [`【${title}】${phase ? ` ${phase}` : ''}`, `思考：${thinking}`, `${resultLabel}：${oneLine}`].join('\n')
}

function buildProcessLog(traces: MultiAgentTraceItem[]): string {
  return traces.map((t) => traceBlockText(t)).join('\n---\n')
}

function lastNLines(text: string, n: number): string {
  const lines = text.split('\n')
  return lines.slice(-n).join('\n')
}

export default function MultiAgent() {
  const [query, setQuery] = useState('')
  const [scene, setScene] = useState<MultiAgentRunRequest['scene']>('finance_research')
  const [symbol, setSymbol] = useState('600519.SH')
  const [timeWindow, setTimeWindow] = useState('近30天')
  const [riskPreference, setRiskPreference] = useState('平衡')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ answer: string; scene: MultiAgentRunRequest['scene']; framework: string } | null>(null)
  const [traces, setTraces] = useState<MultiAgentTraceItem[]>([])
  const [processExpanded, setProcessExpanded] = useState(false)
  const tracesRef = useRef<MultiAgentTraceItem[]>([])
  const processScrollRef = useRef<HTMLDivElement | null>(null)
  const runAbortRef = useRef<AbortController | null>(null)
  const currentScene = useMemo(() => SCENES.find((x) => x.value === scene), [scene])

  const collapsedPreview = useMemo(
    () => lastNLines(buildProcessLog(traces), COLLAPSED_LINE_COUNT),
    [traces]
  )

  useEffect(() => {
    if (!processExpanded || !processScrollRef.current) return
    const el = processScrollRef.current
    el.scrollTop = el.scrollHeight
  }, [traces, processExpanded])

  const run = async () => {
    const q = query.trim()
    if (!q) {
      message.warning('请输入问题')
      return
    }
    runAbortRef.current?.abort()
    const ac = new AbortController()
    runAbortRef.current = ac

    const payload: MultiAgentRunRequest = { query: q, scene }
    if (scene === 'finance_research') {
      payload.finance_params = {
        symbol: symbol.trim(),
        time_window: timeWindow.trim(),
        risk_preference: riskPreference.trim(),
      }
    }

    setLoading(true)
    setResult(null)
    tracesRef.current = []
    setTraces([])

    try {
      await consumeMultiAgentRunStream(payload, {
        signal: ac.signal,
        onEvent: (e) => {
          if (e.type === 'trace') {
            tracesRef.current = [...tracesRef.current, e.item]
            setTraces(tracesRef.current)
          } else if (e.type === 'done') {
            setResult({
              answer: e.answer,
              scene: e.scene as MultiAgentRunRequest['scene'],
              framework: e.framework,
            })
          } else if (e.type === 'error') {
            message.error(e.detail)
          }
        },
      })
    } catch (e: unknown) {
      const err = e as { name?: string; message?: string }
      if (err.name === 'AbortError') return
      message.error(err.message || '执行失败')
    } finally {
      setLoading(false)
    }
  }

  const showProcess = loading || traces.length > 0

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <Card
        title={
          <Space>
            <ClusterOutlined />
            <span>多智能体（CrewAI）</span>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="业务场景驱动的多智能体协作"
            description="每个场景内部都融合 ReAct、Plan & Execute、ReWOO、Reflection 四类单智能体范式。运行后将通过流式接口实时推送「当前在做什么 / 思考 / 输出」；过程区可收起，收起时仅显示末尾 5 行摘要。"
          />
          <div>
            <strong>场景选择</strong>
            <Select
              style={{ width: '100%', marginTop: 8 }}
              value={scene}
              options={SCENES.map((x) => ({ value: x.value, label: x.label }))}
              onChange={(v) => setScene(v)}
            />
            <div style={{ marginTop: 8, color: 'var(--app-text-secondary)' }}>{currentScene?.desc}</div>
          </div>
          <TextArea
            rows={5}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例如：给我一份某上市公司的投研分析，包含基本面、技术面、风险与结论建议。"
          />
          {scene === 'finance_research' && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="股票代码，如 600519.SH / AAPL" />
              <Input value={timeWindow} onChange={(e) => setTimeWindow(e.target.value)} placeholder="时间窗口，如 近30天 / 近4个季度" />
              <Select
                value={riskPreference}
                options={[
                  { label: '保守', value: '保守' },
                  { label: '平衡', value: '平衡' },
                  { label: '进取', value: '进取' },
                ]}
                onChange={(v) => setRiskPreference(v)}
              />
            </Space>
          )}
          <Button type="primary" onClick={run} loading={loading}>
            运行多智能体
          </Button>
        </Space>
      </Card>

      {showProcess && (
        <Card
          title="执行过程"
          style={{ marginTop: 16 }}
          extra={
            <Button type="link" size="small" onClick={() => setProcessExpanded((v) => !v)}>
              {processExpanded ? '收起' : '展开'}
            </Button>
          }
        >
          {loading && traces.length === 0 && (
            <div style={{ marginBottom: 12 }}>
              <Spin size="small" /> <span style={{ marginLeft: 8 }}>正在连接并准备场景…</span>
            </div>
          )}
          <div
            style={
              processExpanded
                ? {
                    maxHeight: 'min(50vh, 440px)',
                    overflowY: 'auto',
                    overflowX: 'hidden',
                    overscrollBehavior: 'contain',
                    touchAction: 'pan-y',
                    isolation: 'isolate',
                    paddingRight: 4,
                  }
                : {
                    height: `${COLLAPSED_LINE_COUNT * LINE_HEIGHT_EM}em`,
                    lineHeight: LINE_HEIGHT_EM,
                    overflow: 'hidden',
                    fontFamily: 'ui-monospace, monospace',
                    fontSize: 13,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }
            }
            ref={processExpanded ? processScrollRef : undefined}
            onWheel={(ev) => {
              if (processExpanded) ev.stopPropagation()
            }}
          >
            {processExpanded ? (
              <List
                size="small"
                dataSource={traces}
                renderItem={(t, idx) => (
                  <List.Item key={idx} style={{ display: 'block', borderBottom: '1px solid var(--app-border, #f0f0f0)' }}>
                    <div style={{ fontWeight: 600 }}>
                      {t.title || t.step || '步骤'}
                      {t.phase ? (
                        <Tag style={{ marginLeft: 8 }} color="blue">
                          {t.phase}
                        </Tag>
                      ) : null}
                    </div>
                    <div style={{ marginTop: 6, color: 'var(--app-text-secondary)' }}>
                      <strong>思考过程：</strong>
                      {t.thinking || '—'}
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <strong>{isMetaTrace(t.step) ? '说明：' : '输出结果：'}</strong>
                      <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{t.output ?? t.text ?? '—'}</div>
                    </div>
                  </List.Item>
                )}
              />
            ) : (
              <span>{collapsedPreview || (loading ? '等待步骤…' : '')}</span>
            )}
          </div>
        </Card>
      )}

      {!loading && result && (
        <Card title="最终答案" style={{ marginTop: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <strong>框架：</strong>
            <Tag color="geekblue" style={{ marginLeft: 8 }}>
              {result.framework}
            </Tag>
            <strong style={{ marginLeft: 12 }}>场景：</strong>
            <Tag color="purple" style={{ marginLeft: 8 }}>
              {result.scene}
            </Tag>
          </div>
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.75 }}>{result.answer}</div>
        </Card>
      )}
    </div>
  )
}
