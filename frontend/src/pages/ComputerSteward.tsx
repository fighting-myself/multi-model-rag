import { useState } from 'react'
import { Card, Input, Button, message, Spin, Collapse, Typography, Alert, Space } from 'antd'
import { DesktopOutlined, SendOutlined } from '@ant-design/icons'
import api from '../services/api'

const { TextArea } = Input
const { Text } = Typography

interface StepItem {
  tool: string
  args: Record<string, unknown>
  result: string
}

interface RunResponse {
  success: boolean
  summary: string
  steps: StepItem[]
  result?: string
  error?: string
}

export default function ComputerSteward() {
  const [instruction, setInstruction] = useState('')
  const [loading, setLoading] = useState(false)
  const [response, setResponse] = useState<RunResponse | null>(null)

  const run = async () => {
    const trim = instruction.trim()
    if (!trim) {
      message.warning('请输入任务目标')
      return
    }
    setLoading(true)
    setResponse(null)
    try {
      const data = await api.post<RunResponse>('/computer-steward/run', { instruction: trim }, { timeout: 300000 })
      setResponse(data)
      if (data.success) message.success('执行完成')
      else message.error(data.error || '执行失败')
    } catch (e: unknown) {
      const err = e as { message?: string }
      message.error(err?.message || '请求失败')
      setResponse({ success: false, summary: '', steps: [], error: err?.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <Card
        title={
          <Space>
            <DesktopOutlined />
            <span>电脑管家</span>
          </Space>
        }
        extra="视觉识别 + AI 决策：看屏幕、移动鼠标、敲键盘，操作整机（任意软件/桌面）。需在有图形界面的环境运行，并结合 .skill 技能综合完成任务"
      >
        <TextArea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="例如：打开记事本，输入 Hello World 并保存到桌面；或：在桌面上双击打开某个文件夹"
          rows={4}
          style={{ marginBottom: 16 }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={run}
          loading={loading}
          disabled={loading}
        >
          执行
        </Button>
      </Card>

      {loading && (
        <Card style={{ marginTop: 16 }}>
          <Spin tip="电脑管家正在看屏并操作，请勿操作鼠标键盘…（可能需 1～3 分钟）" />
        </Card>
      )}

      {!loading && response && (
        <Card title="执行结果" style={{ marginTop: 16 }}>
          {response.success ? (
            <Alert type="success" message="执行成功" showIcon style={{ marginBottom: 16 }} />
          ) : (
            <Alert type="error" message={response.error || '执行失败'} showIcon style={{ marginBottom: 16 }} />
          )}
          {response.summary && (
            <div style={{ marginBottom: 16 }}>
              <Text strong>结果摘要：</Text>
              <div style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>{response.summary}</div>
            </div>
          )}
          {response.steps && response.steps.length > 0 && (
            <Collapse
              items={[
                {
                  key: 'steps',
                  label: `执行步骤（${response.steps.length} 步）`,
                  children: (
                    <ol style={{ margin: 0, paddingLeft: 20 }}>
                      {response.steps.map((s, i) => (
                        <li key={i} style={{ marginBottom: 12 }}>
                          <Text strong>{s.tool}</Text>
                          {Object.keys(s.args).length > 0 && (
                            <div style={{ color: '#666', fontSize: 12 }}>{JSON.stringify(s.args)}</div>
                          )}
                          <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{s.result}</div>
                        </li>
                      ))}
                    </ol>
                  ),
                },
              ]}
            />
          )}
        </Card>
      )}
    </div>
  )
}
