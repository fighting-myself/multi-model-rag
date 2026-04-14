import { useMemo, useState } from 'react'
import { Alert, Button, Card, Input, List, Select, Space, Spin, Tag, message } from 'antd'
import { ClusterOutlined } from '@ant-design/icons'

import api from '../services/api'
import type { MultiAgentRunRequest, MultiAgentRunResponse } from '../types/api'

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

export default function MultiAgent() {
  const [query, setQuery] = useState('')
  const [scene, setScene] = useState<MultiAgentRunRequest['scene']>('finance_research')
  const [symbol, setSymbol] = useState('600519.SH')
  const [timeWindow, setTimeWindow] = useState('近30天')
  const [riskPreference, setRiskPreference] = useState('平衡')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<MultiAgentRunResponse | null>(null)
  const currentScene = useMemo(() => SCENES.find((x) => x.value === scene), [scene])

  const run = async () => {
    const q = query.trim()
    if (!q) {
      message.warning('请输入问题')
      return
    }
    setLoading(true)
    setResult(null)
    try {
      const payload: MultiAgentRunRequest = { query: q, scene }
      if (scene === 'finance_research') {
        payload.finance_params = {
          symbol: symbol.trim(),
          time_window: timeWindow.trim(),
          risk_preference: riskPreference.trim(),
        }
      }
      const data = await api.post<MultiAgentRunResponse>('/multi-agent/run', payload, { timeout: 240000 })
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
            description="每个场景内部都融合 ReAct、Plan & Execute、ReWOO、Reflection 四类单智能体范式。"
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

      {loading && (
        <Card style={{ marginTop: 16 }}>
          <Spin tip="多智能体协作执行中..." />
        </Card>
      )}

      {!loading && result && (
        <Card title="执行结果" style={{ marginTop: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <strong>框架：</strong>
            <Tag color="geekblue" style={{ marginLeft: 8 }}>{result.framework}</Tag>
            <strong style={{ marginLeft: 12 }}>场景：</strong>
            <Tag color="purple" style={{ marginLeft: 8 }}>{result.scene}</Tag>
          </div>
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.75 }}>{result.answer}</div>
          {result.traces?.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <strong>执行轨迹</strong>
              <List
                style={{ marginTop: 8 }}
                bordered
                dataSource={result.traces}
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

