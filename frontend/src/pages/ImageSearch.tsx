import { useState, useEffect, useRef } from 'react'
import { Card, Input, Button, Select, Row, Col, message, Empty } from 'antd'
import { SearchOutlined, PictureOutlined } from '@ant-design/icons'
import api from '../services/api'
import { useAuthStore } from '../stores/authStore'
import type { KnowledgeBaseListResponse } from '../types/api'

interface ImageSearchItem {
  file_id: number
  original_filename: string
  file_type: string
  snippet?: string
}

interface ImageSearchResponse {
  files: ImageSearchItem[]
}

/** 通过带鉴权的 fetch 加载图片，用 data URL 展示（兼容性更好） */
function ImageThumb({ fileId, alt, style }: { fileId: number; alt: string; style?: React.CSSProperties }) {
  const [src, setSrc] = useState<string | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const token = useAuthStore.getState().token
    const url = `/api/v1/files/${fileId}/download`
    fetch(url, {
      method: 'GET',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: 'same-origin',
    })
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status))
        return res.blob()
      })
      .then((blob) => {
        if (!mounted.current || blob.size === 0) return
        const reader = new FileReader()
        reader.onload = () => {
          if (mounted.current && typeof reader.result === 'string') setSrc(reader.result)
        }
        reader.readAsDataURL(blob)
      })
      .catch(() => {})
    return () => {
      mounted.current = false
    }
  }, [fileId])

  if (!src) {
    return (
      <div
        style={{
          width: '100%',
          aspectRatio: '4/3',
          background: '#f5f5f5',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          ...style,
        }}
      >
        <PictureOutlined style={{ fontSize: 32, color: '#bfbfbf' }} />
      </div>
    )
  }
  return <img src={src} alt={alt} style={{ width: '100%', aspectRatio: '4/3', objectFit: 'cover', ...style }} />
}

export default function ImageSearch() {
  const [query, setQuery] = useState('')
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseListResponse['knowledge_bases']>([])
  const [selectedKbId, setSelectedKbId] = useState<number | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<ImageSearchItem[]>([])

  useEffect(() => {
    api
      .get<KnowledgeBaseListResponse>('/knowledge-bases')
      .then((res) => setKnowledgeBases(res.knowledge_bases || []))
      .catch(() => {})
  }, [])

  const handleSearch = async () => {
    const q = (query || '').trim()
    if (!q) {
      message.warning('请输入搜索关键词')
      return
    }
    setLoading(true)
    setResults([])
    try {
      const res = await api.post<ImageSearchResponse>('/search/images', {
        query: q,
        knowledge_base_id: selectedKbId ?? null,
        top_k: 24,
      })
      setResults(res.files || [])
      if (!(res.files?.length)) message.info('未找到匹配的图片')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '搜索失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>以文搜图</h1>
      <Card style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <Input
            placeholder="输入描述或关键词，如：生日蛋糕、海边日落"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={handleSearch}
            style={{ width: 320 }}
            allowClear
          />
          <Select
            placeholder="选择知识库（可选）"
            allowClear
            style={{ width: 220 }}
            value={selectedKbId}
            onChange={setSelectedKbId}
            options={[
              { value: undefined, label: '全部知识库' },
              ...knowledgeBases.map((kb) => ({ value: kb.id, label: `${kb.name}（${kb.chunk_count ?? 0} 块）` })),
            ]}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={handleSearch}>
            搜索
          </Button>
        </div>
      </Card>
      <Card>
        {results.length === 0 && !loading && (
          <Empty description="输入关键词并点击搜索，将在知识库中检索匹配的图片" style={{ padding: 48 }} />
        )}
        {results.length > 0 && (
          <Row gutter={[16, 16]}>
            {results.map((item) => (
              <Col xs={24} sm={12} md={8} lg={6} key={item.file_id}>
                <Card
                  size="small"
                  cover={
                    <ImageThumb fileId={item.file_id} alt={item.original_filename} style={{ borderRadius: '4px 4px 0 0' }} />
                  }
                  actions={[
                    <Button
                      key="download"
                      type="link"
                      size="small"
                      onClick={async () => {
                        try {
                          const blob = await api.get(`/files/${item.file_id}/download`, { responseType: 'blob' }) as Blob
                          if (blob.type.startsWith('application/json') || blob.size === 0) {
                            message.error('文件不存在或无法下载')
                            return
                          }
                          const url = URL.createObjectURL(blob)
                          const a = document.createElement('a')
                          a.href = url
                          a.download = item.original_filename || `file-${item.file_id}`
                          a.click()
                          URL.revokeObjectURL(url)
                          message.success('下载已开始')
                        } catch (e: unknown) {
                          const err = e as { response?: { data?: Blob; status?: number } }
                          const data = err.response?.data
                          const msg = data instanceof Blob && data.type?.includes('json')
                            ? '文件不存在或无权访问'
                            : (err.response?.status === 404 ? '文件不存在' : '下载失败')
                          message.error(msg)
                        }
                      }}
                    >
                      下载
                    </Button>,
                  ]}
                >
                  <Card.Meta
                    title={item.original_filename}
                    description={item.snippet ? <span style={{ fontSize: 12, color: '#666' }}>{item.snippet}</span> : undefined}
                  />
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Card>
    </div>
  )
}
