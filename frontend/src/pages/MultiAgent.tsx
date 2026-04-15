import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Input, Select, Space, Spin, Tag, message } from 'antd'
import { ClusterOutlined } from '@ant-design/icons'

import { consumeMultiAgentRunStream } from '../services/api'
import type { MultiAgentRunRequest } from '../types/api'

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
const PROCESS_EXPANDED_HEIGHT_PX = 440
const SCENE_PARAMS_FIXED_HEIGHT_PX = 120

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
  const [processText, setProcessText] = useState('')
  const [processExpanded, setProcessExpanded] = useState(false)
  const processTextRef = useRef('')
  const streamQueueRef = useRef<string[]>([])
  const streamTimerRef = useRef<number | null>(null)
  const processScrollRef = useRef<HTMLDivElement | null>(null)
  const runAbortRef = useRef<AbortController | null>(null)
  const currentScene = useMemo(() => SCENES.find((x) => x.value === scene), [scene])

  const collapsedPreview = useMemo(
    () => lastNLines(processText, COLLAPSED_LINE_COUNT),
    [processText]
  )

  useEffect(() => {
    if (!processExpanded || !processScrollRef.current) return
    const el = processScrollRef.current
    el.scrollTop = el.scrollHeight
  }, [processText, processExpanded])

  useEffect(() => {
    return () => {
      if (streamTimerRef.current != null) {
        window.clearTimeout(streamTimerRef.current)
      }
    }
  }, [])

  const enqueueStreamText = (text: string) => {
    if (!text) return
    streamQueueRef.current.push(text)
    if (streamTimerRef.current != null) return

    const tick = () => {
      const current = streamQueueRef.current[0]
      if (!current) {
        streamTimerRef.current = null
        return
      }
      const chunkSize = 24
      const nextChunk = current.slice(0, chunkSize)
      const remain = current.slice(chunkSize)
      const nextText = processTextRef.current + nextChunk
      processTextRef.current = nextText
      setProcessText(nextText)
      if (remain.length === 0) {
        streamQueueRef.current.shift()
      } else {
        streamQueueRef.current[0] = remain
      }
      streamTimerRef.current = window.setTimeout(tick, 18)
    }

    streamTimerRef.current = window.setTimeout(tick, 0)
  }

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
    processTextRef.current = ''
    setProcessText('')
    streamQueueRef.current = []
    if (streamTimerRef.current != null) {
      window.clearTimeout(streamTimerRef.current)
      streamTimerRef.current = null
    }

    try {
      await consumeMultiAgentRunStream(payload, {
        signal: ac.signal,
        onEvent: (e) => {
          if (e.type === 'trace') {
            const raw = String(e.item.output ?? e.item.text ?? '').trim()
            if (!raw) return
            const prefix = processTextRef.current || streamQueueRef.current.length > 0 ? '\n\n' : ''
            enqueueStreamText(prefix + raw)
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

  const showProcess = loading || processText.length > 0

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
            description="执行过程仅展示 Task 回调中的原始输出（output.raw），并以流式文本连续追加。"
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
          <div
            style={{
              height: `${SCENE_PARAMS_FIXED_HEIGHT_PX}px`,
              overflow: 'hidden',
            }}
          >
            <Space
              direction="vertical"
              size={8}
              style={{
                width: '100%',
                visibility: scene === 'finance_research' ? 'visible' : 'hidden',
                pointerEvents: scene === 'finance_research' ? 'auto' : 'none',
              }}
              aria-hidden={scene !== 'finance_research'}
            >
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
          </div>
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
          {loading && processText.length === 0 && (
            <div style={{ marginBottom: 12 }}>
              <Spin size="small" /> <span style={{ marginLeft: 8 }}>正在连接并准备场景…</span>
            </div>
          )}
          <div
            style={
              processExpanded
                ? {
                    height: `${PROCESS_EXPANDED_HEIGHT_PX}px`,
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
              <div style={{ whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, monospace', fontSize: 13 }}>
                {processText || (loading ? '等待回调输出…' : '')}
              </div>
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
