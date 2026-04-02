import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Switch, message, Popconfirm, Space } from 'antd'
import { PlusOutlined } from '@ant-design/icons'

import api from '../services/api'
import type { ExternalConnectionItem } from '../types/api'

export default function ExternalConnections() {
  const [list, setList] = useState<ExternalConnectionItem[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ExternalConnectionItem | null>(null)
  const [form] = Form.useForm()

  const fetchList = async () => {
    setLoading(true)
    try {
      const data = await api.get<ExternalConnectionItem[]>('/external-connections')
      setList(Array.isArray(data) ? data : [])
    } catch {
      message.error('获取外接平台连接列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchList()
  }, [])

  const openAdd = () => {
    setEditing(null)
    form.setFieldsValue({
      name: '',
      account: '',
      password: '',
      cookies: '',
      enabled: true,
    })
    setModalOpen(true)
  }

  const openEdit = (record: ExternalConnectionItem) => {
    setEditing(record)
    form.setFieldsValue({
      name: record.name,
      account: record.account ?? '',
      // 脱敏后无法还原真实密码：编辑时留空表示不更新密码
      password: '',
      cookies: '',
      enabled: record.enabled,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const payload: any = {
        name: values.name,
        account: (values.account ?? '').trim() || undefined,
        enabled: values.enabled,
      }
      const pwd = (values.password ?? '').trim()
      if (pwd) payload.password = pwd
      const cookiesRaw = (values.cookies ?? '').trim()
      if (cookiesRaw) payload.cookies = cookiesRaw

      if (editing) {
        await api.put(`/external-connections/${editing.name}`, payload)
        message.success('更新成功')
      } else {
        await api.post('/external-connections', payload)
        message.success('添加成功')
      }
      setModalOpen(false)
      fetchList()
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in (e as any)) return
      message.error('提交失败')
    }
  }

  const handleDelete = async (name: string) => {
    try {
      await api.delete(`/external-connections/${name}`)
      message.success('已删除')
      fetchList()
    } catch {
      message.error('删除失败')
    }
  }

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '账号/用户名', dataIndex: 'account', key: 'account', render: (v: any) => v || '—' },
    {
      title: '密码',
      dataIndex: 'password',
      key: 'password',
      render: (v: any) => (v ? '***' : '—'),
    },
    {
      title: 'Cookies',
      dataIndex: 'cookies_present',
      key: 'cookies_present',
      render: (v: boolean) => (v ? '有' : '无'),
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (v: boolean) => (v ? '是' : '否'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: ExternalConnectionItem) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.name)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div
        style={{
          marginBottom: 20,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div>
          <h1 className="app-page-title" style={{ marginBottom: 4 }}>
            外接平台连接
          </h1>
          <p className="app-page-desc" style={{ marginBottom: 0 }}>
            配置外部平台的账号/密码/Cookies。MCP/Skills 在工具参数里通过 <code>connection_name</code> 匹配并注入。
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
          添加连接
        </Button>
      </div>

      <Table rowKey="id" columns={columns} dataSource={list} loading={loading} pagination={false} />

      <Modal
        title={editing ? '编辑外接平台连接' : '添加外接平台连接'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        width={560}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="连接名称 (connection_name)" rules={[{ required: true }]}>
            <Input placeholder="例如：jira-prod / confluence-aishu / notion-xxx" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="account" label="账号/用户名">
            <Input placeholder="用于 Basic auth 的用户名或登录账号" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码（留空表示不更新）"
            extra="界面仅显示脱敏后的密码状态；提交时留空不会覆盖原密码。"
          >
            <Input.Password placeholder="Basic auth 密码或 API 密钥" />
          </Form.Item>
          <Form.Item name="cookies" label="Cookies（可填 JSON 或原始字符串，可选）">
            <Input.TextArea rows={5} placeholder='{"a":"b"} 或 "a=b; c=d"' />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

