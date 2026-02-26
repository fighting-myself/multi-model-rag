import { useState, useEffect } from 'react'
import { Table, Select, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import api from '../services/api'
import type { AuditLogItem, AuditLogListResponse } from '../types/api'

const ACTION_LABELS: Record<string, string> = {
  create_kb: '创建知识库',
  update_kb: '更新知识库',
  delete_kb: '删除知识库',
  remove_file_from_kb: '从知识库移除文件',
  upload_file: '上传文件',
  delete_file: '删除文件',
}

const RESOURCE_LABELS: Record<string, string> = {
  knowledge_base: '知识库',
  file: '文件',
}

export default function AuditLog() {
  const [data, setData] = useState<AuditLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [actionFilter, setActionFilter] = useState<string | undefined>(undefined)
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string | undefined>(undefined)

  const fetchLogs = async () => {
    setLoading(true)
    try {
      const params: Record<string, string | number> = { page, page_size: pageSize }
      if (actionFilter) params.action = actionFilter
      if (resourceTypeFilter) params.resource_type = resourceTypeFilter
      const res = await api.get<AuditLogListResponse>('/audit-logs', { params })
      setData(res.items || [])
      setTotal(res.total ?? 0)
    } catch {
      message.error('获取审计日志失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchLogs()
  }, [page, pageSize, actionFilter, resourceTypeFilter])

  const columns: ColumnsType<AuditLogItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      dataIndex: 'action',
      key: 'action',
      width: 140,
      render: (v: string) => ACTION_LABELS[v] || v,
    },
    {
      title: '资源类型',
      dataIndex: 'resource_type',
      key: 'resource_type',
      width: 100,
      render: (v: string | null) => (v ? (RESOURCE_LABELS[v] || v) : '-'),
    },
    {
      title: '资源 ID',
      dataIndex: 'resource_id',
      key: 'resource_id',
      width: 100,
    },
    {
      title: '详情',
      dataIndex: 'detail',
      key: 'detail',
      ellipsis: true,
      render: (v: string | null) => {
        if (!v) return '-'
        try {
          const o = JSON.parse(v)
          return typeof o === 'object' ? JSON.stringify(o) : v
        } catch {
          return v
        }
      },
    },
    {
      title: 'IP',
      dataIndex: 'ip',
      key: 'ip',
      width: 120,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
        <div>
          <h1 className="app-page-title" style={{ marginBottom: 4 }}>操作审计</h1>
          <p className="app-page-desc" style={{ marginBottom: 0 }}>关键操作记录，便于排查与合规</p>
        </div>
        <Select
          placeholder="操作类型"
          allowClear
          style={{ width: 160 }}
          value={actionFilter}
          onChange={setActionFilter}
          options={Object.entries(ACTION_LABELS).map(([k, v]) => ({ label: v, value: k }))}
        />
        <Select
          placeholder="资源类型"
          allowClear
          style={{ width: 120 }}
          value={resourceTypeFilter}
          onChange={setResourceTypeFilter}
          options={Object.entries(RESOURCE_LABELS).map(([k, v]) => ({ label: v, value: k }))}
        />
      </div>
      <Table<AuditLogItem>
        rowKey="id"
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
          onChange: (p: number, ps?: number) => {
            setPage(p)
            if (typeof ps === 'number') setPageSize(ps)
          },
        }}
      />
    </div>
  )
}
