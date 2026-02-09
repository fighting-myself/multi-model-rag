import { useState, useEffect } from 'react'
import { Table, Button, Modal, Form, Input, message, Select, Popconfirm } from 'antd'
import { PlusOutlined, FileAddOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { KnowledgeBaseItem, KnowledgeBaseListResponse, FileListResponse, FileItem } from '../types/api'

export default function KnowledgeBases() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [files, setFiles] = useState<FileListResponse['files']>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [addFilesModalVisible, setAddFilesModalVisible] = useState(false)
  const [currentKb, setCurrentKb] = useState<KnowledgeBaseItem | null>(null)
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([])
  const [addFilesLoading, setAddFilesLoading] = useState(false)
  const [form] = Form.useForm()

  const fetchKnowledgeBases = async () => {
    setLoading(true)
    try {
      const response = await api.get<KnowledgeBaseListResponse>('/knowledge-bases')
      setKnowledgeBases(response.knowledge_bases || [])
    } catch {
      message.error('获取知识库列表失败')
    } finally {
      setLoading(false)
    }
  }

  const fetchFiles = async () => {
    try {
      const response = await api.get<FileListResponse>('/files')
      setFiles(response.files || [])
    } catch {
      setFiles([])
    }
  }

  useEffect(() => {
    fetchKnowledgeBases()
  }, [])

  const handleCreate = async (values: { name: string; description?: string }) => {
    try {
      await api.post('/knowledge-bases', values)
      message.success('创建成功')
      setModalVisible(false)
      form.resetFields()
      fetchKnowledgeBases()
    } catch (error) {
      message.error('创建失败')
    }
  }

  const openAddFiles = (record: KnowledgeBaseItem) => {
    setCurrentKb(record)
    setSelectedFileIds([])
    fetchFiles()
    setAddFilesModalVisible(true)
  }

  const handleAddFiles = async () => {
    if (!currentKb || selectedFileIds.length === 0) {
      message.warning('请选择要添加的文件')
      return
    }
    setAddFilesLoading(true)
    try {
      await api.post(`/knowledge-bases/${currentKb.id}/files`, { file_ids: selectedFileIds })
      message.success('已添加文件，正在后台进行 RAG 切分与向量化')
      setAddFilesModalVisible(false)
      setCurrentKb(null)
      fetchKnowledgeBases()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '添加失败')
    } finally {
      setAddFilesLoading(false)
    }
  }

  const handleDelete = async (record: KnowledgeBaseItem) => {
    try {
      await api.delete(`/knowledge-bases/${record.id}`)
      message.success('已删除')
      fetchKnowledgeBases()
    } catch {
      message.error('删除失败')
    }
  }

  const columns = [
    {
      title: '知识库名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
    },
    {
      title: '文件数量',
      dataIndex: 'file_count',
      key: 'file_count',
    },
    {
      title: '分块数量',
      dataIndex: 'chunk_count',
      key: 'chunk_count',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (date: string) => new Date(date).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: KnowledgeBaseItem) => (
        <>
          <Button type="link" size="small" icon={<FileAddOutlined />} onClick={() => openAddFiles(record)}>
            添加文件
          </Button>
          <Popconfirm title="确定删除该知识库？" onConfirm={() => handleDelete(record)}>
            <Button type="link" danger size="small">删除</Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h1>知识库管理</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalVisible(true)}>
          创建知识库
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={knowledgeBases}
        loading={loading}
        rowKey="id"
      />
      <Modal
        title="创建知识库"
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
      >
        <Form form={form} onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`添加文件到知识库「${currentKb?.name}」`}
        open={addFilesModalVisible}
        onCancel={() => { setAddFilesModalVisible(false); setCurrentKb(null) }}
        onOk={handleAddFiles}
        confirmLoading={addFilesLoading}
        okText="添加并切分"
      >
        <p style={{ marginBottom: 8 }}>选择已上传的文件，将进行 RAG 切分与向量化后供智能问答检索。</p>
        <Select
          mode="multiple"
          placeholder="选择文件"
          value={selectedFileIds}
          onChange={setSelectedFileIds}
          style={{ width: '100%' }}
          optionLabelProp="label"
          options={files.map((f: FileItem) => ({ value: f.id, label: f.original_filename || f.filename }))}
        />
      </Modal>
    </div>
  )
}
