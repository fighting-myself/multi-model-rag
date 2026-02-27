import { useState, useEffect } from 'react'
import { Table, Button, Modal, Form, Input, Select, Switch, message, Popconfirm, Drawer, Space } from 'antd'
import { PlusOutlined, ToolOutlined, ApiOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { McpServerItem, McpToolsListResponse } from '../types/api'

const TRANSPORT_OPTIONS = [
  { value: 'streamable_http', label: 'Streamable HTTP (URL)' },
  { value: 'stdio', label: 'Stdio (命令)' },
  { value: 'sse', label: 'SSE (URL)' },
]

export default function McpServers() {
  const [list, setList] = useState<McpServerItem[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<McpServerItem | null>(null)
  const [form] = Form.useForm()
  const [toolsDrawerOpen, setToolsDrawerOpen] = useState(false)
  const [toolsLoading, setToolsLoading] = useState(false)
  const [toolsData, setToolsData] = useState<McpToolsListResponse | null>(null)
  const [currentServerId, setCurrentServerId] = useState<number | null>(null)
  const [testModalOpen, setTestModalOpen] = useState(false)
  const [testToolName, setTestToolName] = useState('')
  const [testArgs, setTestArgs] = useState('{}')
  const [testLoading, setTestLoading] = useState(false)
  const [testResult, setTestResult] = useState<string>('')
  const [mcpAvailable, setMcpAvailable] = useState<boolean | null>(null)

  const fetchList = async () => {
    setLoading(true)
    try {
      const data = await api.get<McpServerItem[]>('/mcp-servers')
      setList(Array.isArray(data) ? data : [])
    } catch {
      message.error('获取 MCP 服务列表失败')
    } finally {
      setLoading(false)
    }
  }

  const checkMcpAvailable = async () => {
    try {
      const res = await api.get<{ available: boolean }>('/mcp-servers/mcp-available')
      setMcpAvailable(res.available)
    } catch {
      setMcpAvailable(false)
    }
  }

  useEffect(() => {
    fetchList()
    checkMcpAvailable()
  }, [])

  const openAdd = () => {
    setEditing(null)
    form.setFieldsValue({ name: '', transport_type: 'streamable_http', config: '{"url": "http://localhost:8000/mcp"}', enabled: true })
    setModalOpen(true)
  }

  /** 快速填充：阿里云百炼 MCP（SSE），鉴权使用环境变量 DASHSCOPE_API_KEY */
  const fillDashScopeMcp = () => {
    form.setFieldsValue({
      name: form.getFieldValue('name') || '阿里云 antv-visualization-chart',
      transport_type: 'sse',
      config: JSON.stringify({
        url: 'https://dashscope.aliyuncs.com/api/v1/mcps/antv-visualization-chart/sse',
        api_key_env: 'DASHSCOPE_API_KEY',
      }, null, 2),
    })
  }

  /** 一键填充 Cursor 格式（阿里云）：mcpServers + type/url/headers，${DASHSCOPE_API_KEY} 由后端从 .env 读取 */
  const fillCursorFormatMcp = () => {
    form.setFieldsValue({
      name: form.getFieldValue('name') || '阿里云 antv-visualization-chart',
      transport_type: 'sse',
      config: JSON.stringify({
        mcpServers: {
          type: 'sse',
          url: 'https://dashscope.aliyuncs.com/api/v1/mcps/antv-visualization-chart/sse',
          headers: {
            Authorization: 'Bearer ${DASHSCOPE_API_KEY}',
          },
        },
      }, null, 2),
    })
  }

  const openEdit = (record: McpServerItem) => {
    setEditing(record)
    form.setFieldsValue({
      name: record.name,
      transport_type: record.transport_type,
      config: JSON.stringify(record.config, null, 2),
      enabled: record.enabled,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      let config: Record<string, unknown>
      try {
        config = JSON.parse(values.config || '{}')
      } catch {
        message.error('config 必须是合法 JSON')
        return
      }
      const payload = { name: values.name, transport_type: values.transport_type, config, enabled: values.enabled }
      if (editing) {
        await api.put(`/mcp-servers/${editing.id}`, payload)
        message.success('更新成功')
      } else {
        await api.post('/mcp-servers', payload)
        message.success('添加成功')
      }
      setModalOpen(false)
      fetchList()
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error(editing ? '更新失败' : '添加失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/mcp-servers/${id}`)
      message.success('已删除')
      fetchList()
    } catch {
      message.error('删除失败')
    }
  }

  const openTools = async (serverId: number) => {
    setCurrentServerId(serverId)
    setToolsDrawerOpen(true)
    setToolsData(null)
    setToolsLoading(true)
    try {
      const data = await api.get<McpToolsListResponse>(`/mcp-servers/${serverId}/tools`)
      setToolsData(data)
    } catch {
      message.error('获取工具列表失败')
    } finally {
      setToolsLoading(false)
    }
  }

  const openTest = (toolName: string) => {
    setTestToolName(toolName)
    setTestArgs('{}')
    setTestResult('')
    setTestModalOpen(true)
  }

  const runTest = async () => {
    if (currentServerId == null) return
    setTestLoading(true)
    setTestResult('')
    try {
      let args: Record<string, unknown>
      try {
        args = JSON.parse(testArgs || '{}')
      } catch {
        setTestResult('arguments 必须是合法 JSON')
        setTestLoading(false)
        return
      }
      const res = await api.post<{ success: boolean; result?: string; error?: string }>(
        `/mcp-servers/${currentServerId}/tools/call`,
        { tool_name: testToolName, arguments: args }
      )
      if (res.success) setTestResult(res.result ?? '')
      else setTestResult('错误: ' + (res.error ?? '未知'))
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setTestResult('请求失败: ' + (err.response?.data?.detail ?? String(e)))
    } finally {
      setTestLoading(false)
    }
  }

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '传输类型', dataIndex: 'transport_type', key: 'transport_type' },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (v: boolean) => (v ? '是' : '否'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: McpServerItem) => (
        <Space>
          <Button type="link" size="small" icon={<ToolOutlined />} onClick={() => openTools(record.id)}>
            工具列表
          </Button>
          <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 className="app-page-title" style={{ marginBottom: 4 }}>MCP 工具管理</h1>
          <p className="app-page-desc" style={{ marginBottom: 0 }}>
            接入外部 MCP 服务后，智能问答在需要时可自动调用这些工具；若不需要则不会调用。
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>添加 MCP 服务</Button>
      </div>
      {mcpAvailable === false && (
        <p style={{ color: '#ef4444', marginBottom: 16 }}>
          当前环境未安装 MCP SDK，无法连接 MCP 服务。请在后端执行: pip install mcp anyio httpx-sse
        </p>
      )}
      <Table rowKey="id" columns={columns} dataSource={list} loading={loading} pagination={false} />

      <Modal
        title={editing ? '编辑 MCP 服务' : '添加 MCP 服务'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        width={560}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="服务名称" rules={[{ required: true }]}>
            <Input placeholder="例如：天气服务" />
          </Form.Item>
          <Form.Item name="transport_type" label="传输类型" rules={[{ required: true }]}>
            <Select options={TRANSPORT_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="config"
            label="配置 (JSON)"
            rules={[{ required: true }]}
            extra={
              <>
                streamable_http/sse 填 url（可选 headers、api_key_env）。支持 Cursor 格式（含 mcpServers）；headers 中的环境变量占位符会从 .env 读取并替换。
                <Button type="link" size="small" onClick={fillDashScopeMcp} style={{ paddingLeft: 4 }}>一键填充阿里云 MCP</Button>
                <Button type="link" size="small" onClick={fillCursorFormatMcp}>Cursor 格式</Button>
              </>
            }
          >
            <Input.TextArea rows={6} placeholder='{"url": "http://localhost:8000/mcp"}' />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title="工具列表"
        open={toolsDrawerOpen}
        onClose={() => setToolsDrawerOpen(false)}
        width={480}
      >
        {toolsLoading && <p>加载中…</p>}
        {!toolsLoading && toolsData && (
          <div>
            <p style={{ marginBottom: 12 }}>服务：{toolsData.server_name}</p>
            {toolsData.tools.length === 0 ? (
              <p>暂无工具</p>
            ) : (
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {toolsData.tools.map((t) => (
                  <li key={t.name} style={{ marginBottom: 16, padding: 12, background: 'var(--app-bg-subtle)', borderRadius: 8 }}>
                    <div style={{ fontWeight: 600 }}>{t.name}</div>
                    {t.description && <div style={{ color: 'var(--app-text-secondary)', fontSize: 12, marginTop: 4 }}>{t.description}</div>}
                    <Button type="link" size="small" icon={<ApiOutlined />} onClick={() => openTest(t.name)} style={{ paddingLeft: 0 }}>
                      测试调用
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Drawer>

      <Modal
        title={`测试工具: ${testToolName}`}
        open={testModalOpen}
        onCancel={() => setTestModalOpen(false)}
        footer={[
          <Button key="run" type="primary" loading={testLoading} onClick={runTest}>执行</Button>,
          <Button key="close" onClick={() => setTestModalOpen(false)}>关闭</Button>,
        ]}
        width={520}
      >
        <Form layout="vertical">
          <Form.Item label="arguments (JSON)">
            <Input.TextArea rows={4} value={testArgs} onChange={(e) => setTestArgs(e.target.value)} />
          </Form.Item>
        </Form>
        {testResult && <pre style={{ background: 'var(--app-bg-muted)', padding: 12, borderRadius: 8, whiteSpace: 'pre-wrap', color: 'var(--app-text-primary)' }}>{testResult}</pre>}
      </Modal>
    </div>
  )
}
