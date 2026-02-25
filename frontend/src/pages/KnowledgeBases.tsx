import { useState, useEffect } from 'react'
import { Table, Button, Modal, Form, Input, message, Select, Popconfirm, Drawer, Space, Progress, List } from 'antd'
import { PlusOutlined, FileAddOutlined, FolderOpenOutlined, DeleteOutlined, ReloadOutlined, EyeOutlined, LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons'
import api from '../services/api'
import { useAuthStore } from '../stores/authStore'
import type {
  KnowledgeBaseItem,
  KnowledgeBaseListResponse,
  FileListResponse,
  FileItem,
  KnowledgeBaseFileListResponse,
  KnowledgeBaseFileItem,
  ChunkListResponse,
  ChunkItem,
  AddFilesToKnowledgeBaseResponse,
} from '../types/api'

export default function KnowledgeBases() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [files, setFiles] = useState<FileListResponse['files']>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [addFilesModalVisible, setAddFilesModalVisible] = useState(false)
  const [currentKb, setCurrentKb] = useState<KnowledgeBaseItem | null>(null)
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([])
  const [addFilesLoading, setAddFilesLoading] = useState(false)
  const [contentDrawerVisible, setContentDrawerVisible] = useState(false)
  const [kbFiles, setKbFiles] = useState<KnowledgeBaseFileItem[]>([])
  const [kbFilesLoading, setKbFilesLoading] = useState(false)
  const [reindexingFileId, setReindexingFileId] = useState<number | null>(null)
  const [chunksModalVisible, setChunksModalVisible] = useState(false)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkItem[]>([])
  const [chunksModalTitle, setChunksModalTitle] = useState('')
  const [form] = Form.useForm()
  /** 添加文件流式进度：{ file_id, filename, status: 'pending'|'processing'|'done'|'skip', reason?, chunk_count? } */
  const [addFilesProgress, setAddFilesProgress] = useState<{ file_id: number; filename: string; status: 'pending' | 'processing' | 'done' | 'skip'; reason?: string; chunk_count?: number }[]>([])

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
    const fileNames = selectedFileIds.map((id) => {
      const f = files.find((x) => x.id === id)
      return f?.original_filename ?? f?.filename ?? `文件 ${id}`
    })
    setAddFilesProgress(
      selectedFileIds.map((file_id, i) => ({
        file_id,
        filename: fileNames[i] ?? `文件 ${file_id}`,
        status: 'pending' as const,
      }))
    )
    setAddFilesLoading(true)
    const token = useAuthStore.getState().token
    try {
      const res = await fetch(`/api/v1/knowledge-bases/${currentKb.id}/files/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ file_ids: selectedFileIds }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || res.statusText)
      }
      const reader = res.body?.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      if (reader) {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n\n')
          buffer = lines.pop() ?? ''
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const data = line.slice(6).trim()
            if (data === '[DONE]') continue
            try {
              const event = JSON.parse(data) as {
                type: string
                file_id?: number
                filename?: string
                reason?: string
                chunk_count?: number
                message?: string
              }
              if (event.type === 'file_start') {
                setAddFilesProgress((prev) =>
                  prev.map((p) =>
                    p.file_id === event.file_id ? { ...p, status: 'processing' as const } : p
                  )
                )
              } else if (event.type === 'file_done') {
                setAddFilesProgress((prev) =>
                  prev.map((p) =>
                    p.file_id === event.file_id
                      ? { ...p, status: 'done' as const, chunk_count: event.chunk_count }
                      : p
                  )
                )
              } else if (event.type === 'file_skip') {
                setAddFilesProgress((prev) =>
                  prev.map((p) =>
                    p.file_id === event.file_id
                      ? { ...p, status: 'skip' as const, reason: event.reason }
                      : p
                  )
                )
              } else if (event.type === 'done') {
                message.success('添加完成')
                fetchKnowledgeBases()
                setAddFilesLoading(false)
              } else if (event.type === 'error') {
                message.error(event.message || '添加失败')
              }
            } catch {
              // ignore parse error
            }
          }
        }
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '添加失败'
      message.error(msg)
      setAddFilesProgress([])
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

  const openContentManage = (record: KnowledgeBaseItem) => {
    setCurrentKb(record)
    setContentDrawerVisible(true)
    fetchKbFiles(record.id)
  }

  const fetchKbFiles = async (kbId: number) => {
    setKbFilesLoading(true)
    try {
      const res = await api.get<KnowledgeBaseFileListResponse>(`/knowledge-bases/${kbId}/files`)
      setKbFiles(res.files || [])
    } catch {
      message.error('获取知识库内容失败')
      setKbFiles([])
    } finally {
      setKbFilesLoading(false)
    }
  }

  const handleRemoveFileFromKb = async (fileId: number) => {
    if (!currentKb) return
    try {
      await api.delete(`/knowledge-bases/${currentKb.id}/files/${fileId}`)
      message.success('已从知识库移除')
      fetchKbFiles(currentKb.id)
      fetchKnowledgeBases()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '移除失败')
    }
  }

  const openChunksModal = async (row: KnowledgeBaseFileItem) => {
    if (!currentKb) return
    setChunksModalTitle(`分块内容：${row.original_filename}`)
    setChunksModalVisible(true)
    setChunks([])
    setChunksLoading(true)
    try {
      const res = await api.get<ChunkListResponse>(`/knowledge-bases/${currentKb.id}/files/${row.file_id}/chunks`)
      setChunks(res.chunks || [])
    } catch {
      message.error('获取分块失败')
      setChunks([])
    } finally {
      setChunksLoading(false)
    }
  }

  const handleReindexFile = async (fileId: number) => {
    if (!currentKb) return
    setReindexingFileId(fileId)
    try {
      await api.post(`/knowledge-bases/${currentKb.id}/files/${fileId}/reindex`)
      message.success('已重新索引，分块与向量已更新')
      fetchKbFiles(currentKb.id)
      fetchKnowledgeBases()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '重新索引失败')
    } finally {
      setReindexingFileId(null)
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
        <Space size="small">
          <Button type="link" size="small" icon={<FolderOpenOutlined />} onClick={() => openContentManage(record)}>
            内容管理
          </Button>
          <Button type="link" size="small" icon={<FileAddOutlined />} onClick={() => openAddFiles(record)}>
            添加文件
          </Button>
          <Popconfirm title="确定删除该知识库？" onConfirm={() => handleDelete(record)}>
            <Button type="link" danger size="small">删除</Button>
          </Popconfirm>
        </Space>
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
        title={addFilesProgress.length > 0 ? `添加进度 · ${currentKb?.name}` : `添加文件到知识库「${currentKb?.name}」`}
        open={addFilesModalVisible}
        onCancel={() => {
          if (!addFilesLoading) {
            setAddFilesModalVisible(false)
            setCurrentKb(null)
            setAddFilesProgress([])
          }
        }}
        onOk={handleAddFiles}
        confirmLoading={addFilesLoading}
        okText={addFilesProgress.length > 0 ? '添加并切分' : '添加并切分'}
        okButtonProps={{ style: { display: addFilesProgress.length > 0 ? 'none' : undefined } }}
      >
        {addFilesProgress.length === 0 ? (
          <>
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
          </>
        ) : (
          <div style={{ minHeight: 280 }}>
            <p style={{ marginBottom: 12, color: '#666', fontSize: 13 }}>
              {addFilesLoading ? '正在切分与向量化，请稍候…' : '处理完成'}
            </p>
            <List
              size="small"
              bordered
              style={{ maxHeight: 320, overflow: 'auto' }}
              dataSource={addFilesProgress}
              renderItem={(item) => (
                <List.Item>
                  <Space align="start" style={{ width: '100%' }}>
                    <span style={{ width: 24, display: 'inline-flex', justifyContent: 'center' }}>
                      {item.status === 'pending' && <LoadingOutlined spin style={{ color: '#bfbfbf' }} />}
                      {item.status === 'processing' && <LoadingOutlined spin style={{ color: '#1890ff' }} />}
                      {item.status === 'done' && <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                      {item.status === 'skip' && <CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 500, marginBottom: 2 }}>{item.filename}</div>
                      <div style={{ fontSize: 12, color: '#666' }}>
                        {item.status === 'pending' && <span style={{ color: '#999' }}>等待中</span>}
                        {item.status === 'processing' && <span style={{ color: '#1890ff' }}>处理中</span>}
                        {item.status === 'done' && item.chunk_count != null && (
                          <span style={{ color: '#52c41a' }}>成功 · {item.chunk_count} 块</span>
                        )}
                        {item.status === 'skip' && item.reason && (
                          <span style={{ color: '#ff4d4f' }}>跳过：{item.reason}</span>
                        )}
                      </div>
                    </div>
                  </Space>
                </List.Item>
              )}
            />
            {!addFilesLoading && addFilesProgress.length > 0 && (
              <div style={{ marginTop: 16, textAlign: 'right' }}>
                <Button type="primary" onClick={() => { setAddFilesModalVisible(false); setCurrentKb(null); setAddFilesProgress([]); fetchKnowledgeBases() }}>
                  关闭
                </Button>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Drawer
        title={`知识库「${currentKb?.name}」内容管理`}
        width={720}
        open={contentDrawerVisible}
        onClose={() => { setContentDrawerVisible(false); setCurrentKb(null) }}
        extra={
          currentKb && (
            <Button type="primary" icon={<FileAddOutlined />} onClick={() => { setContentDrawerVisible(false); openAddFiles(currentKb) }}>
              添加文件
            </Button>
          )
        }
      >
        <p style={{ marginBottom: 16, color: '#666' }}>
          对本知识库内的文件进行查看、移除或重新索引。分块有问题时可使用「重新索引」重新切分与向量化。
        </p>
        <Table
          rowKey="file_id"
          loading={kbFilesLoading}
          dataSource={kbFiles}
          pagination={false}
          size="small"
          columns={[
            { title: '文件名', dataIndex: 'original_filename', key: 'original_filename', ellipsis: true },
            { title: '类型', dataIndex: 'file_type', key: 'file_type', width: 80 },
            { title: '分块数', dataIndex: 'chunk_count_in_kb', key: 'chunk_count_in_kb', width: 80 },
            {
              title: '添加时间',
              dataIndex: 'added_at',
              key: 'added_at',
              width: 160,
              render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-',
            },
            {
              title: '操作',
              key: 'action',
              width: 220,
              render: (_: unknown, row: KnowledgeBaseFileItem) => (
                <Space size="small" wrap>
                  <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => openChunksModal(row)}>
                    查看分块
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={reindexingFileId === row.file_id}
                    onClick={() => handleReindexFile(row.file_id)}
                  >
                    重新索引
                  </Button>
                  <Popconfirm
                    title="确定从本知识库移除该文件？分块与向量将被删除。"
                    onConfirm={() => handleRemoveFileFromKb(row.file_id)}
                  >
                    <Button type="link" danger size="small" icon={<DeleteOutlined />}>移除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
        {kbFiles.length === 0 && !kbFilesLoading && (
          <div style={{ textAlign: 'center', padding: 24, color: '#999' }}>暂无文件，可点击「添加文件」加入内容</div>
        )}
      </Drawer>

      <Modal
        title={chunksModalTitle}
        open={chunksModalVisible}
        onCancel={() => setChunksModalVisible(false)}
        footer={null}
        width={720}
      >
        {chunksLoading ? (
          <div style={{ padding: 24, textAlign: 'center' }}>加载中...</div>
        ) : chunks.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: '#999' }}>暂无分块内容</div>
        ) : (
          <div style={{ maxHeight: 480, overflowY: 'auto' }}>
            {chunks.map((c) => (
              <div
                key={c.id}
                style={{
                  marginBottom: 16,
                  padding: 12,
                  background: '#fafafa',
                  borderRadius: 4,
                  border: '1px solid #f0f0f0',
                }}
              >
                <div style={{ marginBottom: 6, fontSize: 12, color: '#666' }}>分块 #{c.chunk_index + 1}</div>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13 }}>
                  {c.content || '(空)'}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}
