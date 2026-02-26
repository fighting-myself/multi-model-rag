import { useState, useEffect } from 'react'
import { Table, Button, Upload, message, Space, Popconfirm } from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import api from '../services/api'
import { useAuthStore, type AuthState } from '../stores/authStore'
import type { FileItem, FileListResponse } from '../types/api'

export default function Files() {
  const [files, setFiles] = useState<FileItem[]>([])
  const [loading, setLoading] = useState(false)
  const token = useAuthStore((s: AuthState) => s.token)

  const fetchFiles = async () => {
    setLoading(true)
    try {
      const response = await api.get<FileListResponse>('/files')
      setFiles(response.files || [])
    } catch {
      message.error('获取文件列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchFiles()
  }, [])

  const handleDownload = async (record: FileItem) => {
    if (!token) return
    try {
      const res = await fetch(`/api/v1/files/${record.id}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('下载失败')
      const blob = await res.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = record.original_filename || record.filename || 'download'
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('下载已开始')
    } catch {
      message.error('下载失败')
    }
  }

  const handleDelete = async (record: FileItem) => {
    try {
      await api.delete(`/files/${record.id}`)
      message.success('已删除')
      fetchFiles()
    } catch {
      message.error('删除失败')
    }
  }

  /** 解析后端返回的错误 detail（可能是字符串或 422 校验数组） */
  const getUploadErrorDetail = (res: unknown): string => {
    if (res == null) return '上传失败'
    const d = (res as { detail?: string | Array<{ msg?: string; loc?: unknown[] }> }).detail
    if (typeof d === 'string') return d
    if (Array.isArray(d) && d.length > 0) {
      const first = d[0]
      return (first && typeof first === 'object' && 'msg' in first ? first.msg : String(d[0])) as string
    }
    return '上传失败'
  }

  const uploadProps: UploadProps = {
    name: 'file',
    action: '/api/v1/files/upload',
    headers: {
      Authorization: token ? `Bearer ${token}` : '',
    },
    onChange(info: Parameters<NonNullable<UploadProps['onChange']>>[0]) {
      if (info.file.status === 'done') {
        message.success(`${info.file.name} 上传成功`)
        fetchFiles()
      } else if (info.file.status === 'error') {
        message.error(getUploadErrorDetail(info.file.response) || `${info.file.name} 上传失败`)
      }
    },
  }

  const columns = [
    {
      title: '文件名',
      dataIndex: 'original_filename',
      key: 'original_filename',
    },
    {
      title: '文件类型',
      dataIndex: 'file_type',
      key: 'file_type',
    },
    {
      title: '文件大小',
      dataIndex: 'file_size',
      key: 'file_size',
      render: (size: number) => (size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(2)} MB` : `${(size / 1024).toFixed(2)} KB`),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (date: string) => new Date(date).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: FileItem) => (
        <Space>
          <Button type="link" size="small" onClick={() => handleDownload(record)}>下载</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record)}>
            <Button type="link" danger size="small">删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h1 style={{ margin: 0 }}>文件管理</h1>
          <Upload {...uploadProps}>
            <Button type="primary" icon={<UploadOutlined />}>
              上传文件
            </Button>
          </Upload>
        </div>
        <p style={{ color: '#666', fontSize: 12, margin: 0 }}>
          支持格式：PDF、Word、Excel、PPT、TXT、Markdown、图片等；单文件不超过 100MB；文件名长度不超过 200 字符；禁止 exe、bat、脚本等危险类型。上传前会校验文件真实类型与安全策略。
        </p>
      </div>
      <Table
        columns={columns}
        dataSource={files}
        loading={loading}
        rowKey="id"
      />
    </div>
  )
}
