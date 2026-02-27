import { useState, useEffect, useRef } from 'react'
import { Table, Button, Modal, Form, Input, message, Select, Popconfirm, Drawer, Space, List, Collapse, Checkbox, Dropdown } from 'antd'
import { PlusOutlined, FileAddOutlined, FolderOpenOutlined, DeleteOutlined, ReloadOutlined, EyeOutlined, LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined, EditOutlined, ExportOutlined, CloudUploadOutlined } from '@ant-design/icons'
import api, { fetchWithAuth, streamPost } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import PageSkeleton from '../components/PageSkeleton'
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
  TaskEnqueueResponse,
  TaskStatusResponse,
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
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [editingKb, setEditingKb] = useState<KnowledgeBaseItem | null>(null)
  const [addFilesInBackground, setAddFilesInBackground] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskStatus, setTaskStatus] = useState<TaskStatusResponse | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollCountRef = useRef(0)
  const POLL_MAX = 150 // 约 5 分钟（每 2 秒一次），超时后停止轮询
  const [exportingKbId, setExportingKbId] = useState<number | null>(null)
  const [reindexAllTaskId, setReindexAllTaskId] = useState<string | null>(null)
  const [reindexFileTaskId, setReindexFileTaskId] = useState<string | null>(null)
  /** 当前提交了「重新索引（后台）」的文件 id，仅该行显示 loading */
  const [reindexFileId, setReindexFileId] = useState<number | null>(null)

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

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const payload: Record<string, unknown> = {
        name: values.name,
        description: values.description,
        chunk_size: values.chunk_size ? Number(values.chunk_size) : undefined,
        chunk_overlap: values.chunk_overlap ? Number(values.chunk_overlap) : undefined,
        chunk_max_expand_ratio: values.chunk_max_expand_ratio ? Number(values.chunk_max_expand_ratio) : undefined,
        embedding_model: values.embedding_model || undefined,
        llm_model: values.llm_model || undefined,
        temperature: values.temperature != null && values.temperature !== '' ? Number(values.temperature) : undefined,
        enable_rerank: values.enable_rerank !== false,
        enable_hybrid: values.enable_hybrid !== false,
      }
      await api.post('/knowledge-bases', payload)
      message.success('创建成功')
      setModalVisible(false)
      form.resetFields()
      fetchKnowledgeBases()
    } catch {
      message.error('创建失败')
    }
  }

  const openEdit = (record: KnowledgeBaseItem) => {
    setEditingKb(record)
    form.setFieldsValue({
      name: record.name,
      description: record.description ?? '',
      chunk_size: record.chunk_size ?? undefined,
      chunk_overlap: record.chunk_overlap ?? undefined,
      chunk_max_expand_ratio: record.chunk_max_expand_ratio ?? undefined,
      embedding_model: record.embedding_model ?? undefined,
      llm_model: record.llm_model ?? undefined,
      temperature: record.temperature ?? undefined,
      enable_rerank: record.enable_rerank !== false,
      enable_hybrid: record.enable_hybrid !== false,
    })
    setEditModalVisible(true)
  }

  const handleUpdate = async (values: Record<string, unknown>) => {
    if (!editingKb) return
    try {
      const payload: Record<string, unknown> = {
        name: values.name,
        description: values.description,
        chunk_size: values.chunk_size ? Number(values.chunk_size) : undefined,
        chunk_overlap: values.chunk_overlap ? Number(values.chunk_overlap) : undefined,
        chunk_max_expand_ratio: values.chunk_max_expand_ratio ? Number(values.chunk_max_expand_ratio) : undefined,
        embedding_model: values.embedding_model || undefined,
        llm_model: values.llm_model || undefined,
        temperature: values.temperature != null && values.temperature !== '' ? Number(values.temperature) : undefined,
        enable_rerank: values.enable_rerank !== false,
        enable_hybrid: values.enable_hybrid !== false,
      }
      await api.put(`/knowledge-bases/${editingKb.id}`, payload)
      message.success('更新成功')
      setEditModalVisible(false)
      setEditingKb(null)
      form.resetFields()
      fetchKnowledgeBases()
      if (currentKb?.id === editingKb.id) setCurrentKb({ ...currentKb, ...payload } as KnowledgeBaseItem)
    } catch {
      message.error('更新失败')
    }
  }

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    setTaskId(null)
    setReindexAllTaskId(null)
    setReindexFileTaskId(null)
    setReindexFileId(null)
    setTaskStatus(null)
  }

  const pollTask = async (tid: string) => {
    pollCountRef.current += 1
    if (pollCountRef.current >= POLL_MAX) {
      stopPolling()
      message.warning('任务状态轮询超时（约 5 分钟）。若未启动 Celery Worker，任务不会执行；可稍后刷新页面查看。')
      return
    }
    try {
      const res = await api.get<TaskStatusResponse>(`/tasks/${tid}`)
      setTaskStatus(res)
      if (res.status === 'SUCCESS') {
        if (pollRef.current) clearInterval(pollRef.current)
        pollRef.current = null
        message.success('任务完成')
        fetchKnowledgeBases()
        if (currentKb) fetchKbFiles(currentKb.id)
        setTaskId(null)
        setReindexAllTaskId(null)
        setReindexFileTaskId(null)
        setReindexFileId(null)
      } else if (res.status === 'FAILURE') {
        if (pollRef.current) clearInterval(pollRef.current)
        pollRef.current = null
        message.error(res.error || '任务失败')
        setTaskId(null)
        setReindexAllTaskId(null)
        setReindexFileTaskId(null)
        setReindexFileId(null)
      }
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

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
    if (addFilesInBackground) {
      setAddFilesLoading(true)
      setTaskStatus(null)
      try {
        const res = await api.post<TaskEnqueueResponse>(`/knowledge-bases/${currentKb.id}/files/async`, { file_ids: selectedFileIds })
        if (res.sync || !res.task_id) {
          message.success(res.message || '已同步执行完成')
          setAddFilesModalVisible(false)
          setSelectedFileIds([])
          setCurrentKb(null)
          fetchKnowledgeBases()
          return
        }
        const tid = res.task_id
        pollCountRef.current = 0
        setTaskId(tid)
        message.info('任务已提交，正在后台执行')
        setAddFilesModalVisible(false)
        setSelectedFileIds([])
        setCurrentKb(null)
        pollRef.current = setInterval(() => pollTask(tid), 2000)
      } catch (e: unknown) {
        const err = e as { response?: { data?: { detail?: string } } }
        message.error(err.response?.data?.detail || '提交失败')
      } finally {
        setAddFilesLoading(false)
      }
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
    try {
      const { reader } = await streamPost(`knowledge-bases/${currentKb.id}/files/stream`, { file_ids: selectedFileIds })
      const decoder = new TextDecoder()
      let buffer = ''
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

  const handleReindexFileAsync = async (fileId: number) => {
    if (!currentKb) return
    try {
      const res = await api.post<TaskEnqueueResponse>(`/knowledge-bases/${currentKb.id}/files/${fileId}/reindex-async`)
      if (res.sync || !res.task_id) {
        message.success(res.message || '已同步执行完成')
        setReindexFileId(null)
        fetchKbFiles(currentKb.id)
        fetchKnowledgeBases()
        return
      }
      pollCountRef.current = 0
      setReindexFileId(fileId)
      setReindexFileTaskId(res.task_id)
      message.info('已提交「仅当前文件」重新索引，请等待 Worker 执行')
      pollRef.current = setInterval(() => pollTask(res.task_id!), 2000)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '提交失败')
    }
  }

  const handleReindexAllAsync = async () => {
    if (!currentKb) return
    try {
      const res = await api.post<TaskEnqueueResponse>(`/knowledge-bases/${currentKb.id}/reindex-all-async`)
      if (res.sync || !res.task_id) {
        message.success(res.message || '已同步执行完成')
        fetchKbFiles(currentKb.id)
        fetchKnowledgeBases()
        return
      }
      pollCountRef.current = 0
      setReindexAllTaskId(res.task_id)
      message.info('已提交「全库」重索引，将处理本知识库下全部文件')
      pollRef.current = setInterval(() => pollTask(res.task_id!), 2000)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '提交失败')
    }
  }

  const handleExport = async (kbId: number, format: 'json' | 'zip') => {
    setExportingKbId(kbId)
    try {
      const res = await fetchWithAuth(`/api/v1/knowledge-bases/${kbId}/export?format=${format}`)
      if (!res.ok) throw new Error(res.statusText)
      const blob = await res.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const ts = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)
      a.download = `kb_${kbId}_export_${ts}.${format === 'zip' ? 'zip' : 'json'}`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    } finally {
      setExportingKbId(null)
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
        <Space size="small" wrap>
          <Button type="link" size="small" icon={<FolderOpenOutlined />} onClick={() => openContentManage(record)}>
            内容管理
          </Button>
          <Button type="link" size="small" icon={<FileAddOutlined />} onClick={() => openAddFiles(record)}>
            添加文件
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Dropdown
            menu={{
              items: [
                { key: 'json', label: '导出 JSON', onClick: () => handleExport(record.id, 'json') },
                { key: 'zip', label: '导出 ZIP', onClick: () => handleExport(record.id, 'zip') },
              ],
            }}
          >
            <Button type="link" size="small" icon={<ExportOutlined />} loading={exportingKbId === record.id}>
              导出
            </Button>
          </Dropdown>
          <Popconfirm title="确定删除该知识库？" onConfirm={() => handleDelete(record)}>
            <Button type="link" danger size="small">删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  if (loading && knowledgeBases.length === 0) return <PageSkeleton rows={6} />

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 className="app-page-title" style={{ marginBottom: 4 }}>知识库管理</h1>
          <p className="app-page-desc" style={{ marginBottom: 0 }}>创建知识库并添加文件，供检索与智能问答使用</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setModalVisible(true) }} size="large">
          创建知识库
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={knowledgeBases}
        loading={loading}
        rowKey="id"
        scroll={{ x: 'max-content' }}
      />
      {(taskId || reindexAllTaskId || reindexFileTaskId) && taskStatus && (
        <div style={{ marginBottom: 16, padding: 12, background: taskStatus.status === 'SUCCESS' ? 'var(--app-success-bg)' : taskStatus.status === 'FAILURE' ? 'var(--app-error-bg)' : 'var(--app-info-bg)', border: '1px solid var(--app-border-info)', borderRadius: 8, color: 'var(--app-text-primary)' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
            <Space wrap>
              <span>后台任务：{taskStatus.status}</span>
              {reindexAllTaskId && <span style={{ color: 'var(--app-warning-text)', fontSize: 12 }}>（全库重索引）</span>}
              {reindexFileTaskId && !reindexAllTaskId && <span style={{ color: 'var(--app-text-muted)', fontSize: 12 }}>（仅单个文件）</span>}
              {taskStatus.status === 'SUCCESS' && taskStatus.result && (
                <span>文件数 {taskStatus.result.file_count}，分块数 {taskStatus.result.chunk_count}</span>
              )}
              {taskStatus.status === 'FAILURE' && taskStatus.error && <span style={{ color: '#ff4d4f' }}>{taskStatus.error}</span>}
              {(taskStatus.status === 'PENDING' || taskStatus.status === 'STARTED') && (
                <span style={{ color: 'var(--app-text-muted)', fontSize: 12 }}>
                  PENDING 表示任务在队列中等待执行。若一直不变，请启动 Celery Worker：<code style={{ fontSize: 11 }}> celery -A app.celery_app worker -l info --pool=solo</code>
                </span>
              )}
            </Space>
            <Button size="small" onClick={stopPolling}>关闭</Button>
          </Space>
        </div>
      )}

      <Modal
        title="创建知识库"
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        width={560}
      >
        <Form form={form} onFinish={handleCreate} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Collapse ghost items={[
            {
              key: '1',
              label: '高级配置（分块、模型、检索）',
              children: (
                <>
                  <Form.Item name="chunk_size" label="分块大小（字符）">
                    <Input type="number" placeholder="留空用全局默认" />
                  </Form.Item>
                  <Form.Item name="chunk_overlap" label="重叠字符数">
                    <Input type="number" placeholder="留空用全局默认" />
                  </Form.Item>
                  <Form.Item name="chunk_max_expand_ratio" label="最大扩展比例">
                    <Input type="number" step={0.1} placeholder="如 1.3，留空用默认" />
                  </Form.Item>
                  <Form.Item name="embedding_model" label="嵌入模型">
                    <Input placeholder="留空用全局" />
                  </Form.Item>
                  <Form.Item name="llm_model" label="LLM 模型">
                    <Input placeholder="留空用全局" />
                  </Form.Item>
                  <Form.Item name="temperature" label="温度 (0~2)">
                    <Input type="number" step={0.1} min={0} max={2} placeholder="留空用默认" />
                  </Form.Item>
                  <Form.Item name="enable_rerank" valuePropName="checked" noStyle>
                    <Checkbox>启用 Rerank 重排序</Checkbox>
                  </Form.Item>
                  <Form.Item name="enable_hybrid" valuePropName="checked" noStyle style={{ marginLeft: 16 }}>
                    <Checkbox>启用混合检索（向量+全文）</Checkbox>
                  </Form.Item>
                </>
              ),
            },
          ]} />
        </Form>
      </Modal>

      <Modal
        title="编辑知识库"
        open={editModalVisible}
        onCancel={() => { setEditModalVisible(false); setEditingKb(null); form.resetFields() }}
        onOk={() => form.submit()}
        width={560}
      >
        <Form form={form} onFinish={handleUpdate} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Collapse ghost items={[
            {
              key: '1',
              label: '高级配置（分块、模型、检索）',
              children: (
                <>
                  <Form.Item name="chunk_size" label="分块大小（字符）">
                    <Input type="number" placeholder="留空用全局默认" />
                  </Form.Item>
                  <Form.Item name="chunk_overlap" label="重叠字符数">
                    <Input type="number" placeholder="留空用全局默认" />
                  </Form.Item>
                  <Form.Item name="chunk_max_expand_ratio" label="最大扩展比例">
                    <Input type="number" step={0.1} placeholder="如 1.3" />
                  </Form.Item>
                  <Form.Item name="embedding_model" label="嵌入模型">
                    <Input placeholder="留空用全局" />
                  </Form.Item>
                  <Form.Item name="llm_model" label="LLM 模型">
                    <Input placeholder="留空用全局" />
                  </Form.Item>
                  <Form.Item name="temperature" label="温度 (0~2)">
                    <Input type="number" step={0.1} min={0} max={2} placeholder="留空用默认" />
                  </Form.Item>
                  <Form.Item name="enable_rerank" valuePropName="checked" noStyle>
                    <Checkbox>启用 Rerank 重排序</Checkbox>
                  </Form.Item>
                  <Form.Item name="enable_hybrid" valuePropName="checked" noStyle style={{ marginLeft: 16 }}>
                    <Checkbox>启用混合检索（向量+全文）</Checkbox>
                  </Form.Item>
                </>
              ),
            },
          ]} />
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
            <Form.Item style={{ marginTop: 12, marginBottom: 0 }}>
              <Checkbox checked={addFilesInBackground} onChange={(e) => setAddFilesInBackground(e.target.checked)}>
                后台执行（接口立即返回，任务在后台执行，可在页面顶部查看任务状态）
              </Checkbox>
            </Form.Item>
          </>
        ) : (
          <div style={{ minHeight: 280 }}>
            <p style={{ marginBottom: 12, color: 'var(--app-text-muted)', fontSize: 13 }}>
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
                      <div style={{ fontSize: 12, color: 'var(--app-text-muted)' }}>
                        {item.status === 'pending' && <span style={{ color: 'var(--app-text-muted)' }}>等待中</span>}
                        {item.status === 'processing' && <span style={{ color: 'var(--app-accent)' }}>处理中</span>}
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
            <Space>
              <Dropdown
                menu={{
                  items: [
                    { key: 'json', label: '导出 JSON', onClick: () => handleExport(currentKb.id, 'json') },
                    { key: 'zip', label: '导出 ZIP', onClick: () => handleExport(currentKb.id, 'zip') },
                  ],
                }}
              >
                <Button icon={<ExportOutlined />} loading={exportingKbId === currentKb.id}>导出</Button>
              </Dropdown>
              <Popconfirm
                title="确定对本知识库下全部文件执行重新索引？仅需重索引单个文件时，请使用表格中该行的「重新索引（后台）」"
                onConfirm={handleReindexAllAsync}
              >
                <Button loading={!!reindexAllTaskId} icon={<CloudUploadOutlined />}>
                  全库重索引（后台）
                </Button>
              </Popconfirm>
              <Button type="primary" icon={<FileAddOutlined />} onClick={() => { setContentDrawerVisible(false); openAddFiles(currentKb) }}>
                添加文件
              </Button>
            </Space>
          )
        }
      >
        <p style={{ marginBottom: 16, color: 'var(--app-text-muted)' }}>
          对本知识库内的文件进行查看、移除或重新索引。分块有问题时可使用「重新索引」重新切分与向量化。支持「全库重索引（后台）」与单文件「重新索引（后台）」。
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
                  <Button
                    type="link"
                    size="small"
                    loading={reindexFileTaskId !== null && reindexFileId === row.file_id}
                    onClick={() => handleReindexFileAsync(row.file_id)}
                  >
                    重新索引（后台）
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
          <div style={{ textAlign: 'center', padding: 24, color: 'var(--app-text-muted)' }}>暂无文件，可点击「添加文件」加入内容</div>
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
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--app-text-muted)' }}>暂无分块内容</div>
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
                <div style={{ marginBottom: 6, fontSize: 12, color: 'var(--app-text-muted)' }}>分块 #{c.chunk_index + 1}</div>
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
