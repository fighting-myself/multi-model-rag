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
        message.error(info.file.response?.detail || `${info.file.name} 上传失败`)
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
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h1>文件管理</h1>
        <Upload {...uploadProps}>
          <Button type="primary" icon={<UploadOutlined />}>
            上传文件
          </Button>
        </Upload>
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
