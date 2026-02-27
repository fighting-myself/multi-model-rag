import { useState } from 'react'
import { Card, Input, Button, message, Spin, Collapse, Typography, Space, Alert } from 'antd'
import { RobotOutlined, SendOutlined } from '@ant-design/icons'
import api from '../services/api'

const { TextArea } = Input
const { Text } = Typography

interface StewardStep {
  tool: string
  args: Record<string, unknown>
  result: string
}

interface StewardRunResponse {
  success: boolean
  summary: string
  steps: StewardStep[]
  result?: string
  error?: string
}

export default function Steward() {
  const [instruction, setInstruction] = useState('')
  const [loading, setLoading] = useState(false)
  const [response, setResponse] = useState<StewardRunResponse | null>(null)

  const run = async () => {
    const trim = instruction.trim()
    if (!trim) {
      message.warning('请输入指令')
      return
    }
    setLoading(true)
    setResponse(null)
    try {
      // 查票、多步操作等可能需 1～5 分钟
      const data = await api.post<StewardRunResponse>('/steward/run', { instruction: trim }, { timeout: 300000 })
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
            <RobotOutlined />
            <span>浏览器助手</span>
          </Space>
        }
        extra="根据指令在浏览器中执行操作（如打开网页、查票、获取 Cookie 等）。复杂任务如 12306 查票可能需 1～5 分钟，请耐心等待"
      >
        <TextArea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="例如：打开 https://example.com 登录，账号 admin，密码 123456，登录后把 Cookie 返回给我"
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
          <Spin tip="浏览器助手正在执行，请稍候…（复杂任务可能需 1～5 分钟，请勿关闭）" />
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
