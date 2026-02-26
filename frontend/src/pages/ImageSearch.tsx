import { useState, useEffect, useRef } from 'react'
import { Card, Input, Button, Select, Row, Col, message, Empty, Tabs, Upload } from 'antd'
import { SearchOutlined, PictureOutlined, UploadOutlined, FileTextOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd/es/upload/interface'
import api from '../services/api'
import type {
  KnowledgeBaseListResponse,
  ImageSearchItem,
  ImageSearchResponse,
  UnifiedSearchItem,
  UnifiedSearchResponse,
} from '../types/api'

/** 通过 api 实例加载图片（与后端同源、带鉴权），用 Object URL 展示 */
function ImageThumb({ fileId, alt, style }: { fileId: number; alt: string; style?: React.CSSProperties }) {
  const [src, setSrc] = useState<string | null>(null)
  const mounted = useRef(true)
  const urlRef = useRef<string | null>(null)

  useEffect(() => {
    mounted.current = true
    const id = Number(fileId)
    if (!id || id <= 0) return
    api
      .get<Blob>(`/files/${id}/download`, { responseType: 'blob' })
      .then((blob: Blob) => {
        if (!mounted.current || !blob || blob.size === 0) return
        if (blob.type && blob.type.toLowerCase().includes('json')) return
        const url = URL.createObjectURL(blob)
        urlRef.current = url
        setSrc(url)
      })
      .catch(() => {})
    return () => {
      mounted.current = false
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current)
        urlRef.current = null
      }
      setSrc(null)
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

type TabMode = 'text' | 'image' | 'unified'

export default function ImageSearch() {
  const [tab, setTab] = useState<TabMode>('text')
  const [query, setQuery] = useState('')
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseListResponse['knowledge_bases']>([])
  const [selectedKbId, setSelectedKbId] = useState<number | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [imageFiles, setImageFiles] = useState<UploadFile[]>([])
  const [results, setResults] = useState<ImageSearchItem[]>([])
  const [unifiedResults, setUnifiedResults] = useState<UnifiedSearchItem[]>([])

  useEffect(() => {
    api
      .get<KnowledgeBaseListResponse>('/knowledge-bases')
      .then((res) => setKnowledgeBases(res.knowledge_bases || []))
      .catch(() => {})
  }, [])

  const handleTextSearch = async () => {
    const q = (query || '').trim()
    if (!q) {
      message.warning('请输入搜索关键词')
      return
    }
    setLoading(true)
    setResults([])
    setUnifiedResults([])
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

  const handleImageSearch = async () => {
    const file = imageFiles[0]?.originFileObj
    if (!file) {
      message.warning('请先上传一张图片')
      return
    }
    setLoading(true)
    setResults([])
    setUnifiedResults([])
    try {
      const fd = new FormData()
      fd.append('file', file)
      if (selectedKbId != null) fd.append('knowledge_base_id', String(selectedKbId))
      fd.append('top_k', '24')
      const res = await api.post<ImageSearchResponse>('/search/by-image/upload', fd)
      setResults(res.files || [])
      if (!(res.files?.length)) message.info('未找到相似图片')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '图搜图失败')
    } finally {
      setLoading(false)
    }
  }

  const handleUnifiedSearch = async () => {
    const q = (query || '').trim()
    const file = imageFiles[0]?.originFileObj
    if (!q && !file) {
      message.warning('请输入关键词或上传一张图片')
      return
    }
    setLoading(true)
    setResults([])
    setUnifiedResults([])
    try {
      if (file) {
        const reader = new FileReader()
        const base64 = await new Promise<string>((resolve, reject) => {
          reader.onload = () => {
            const result = reader.result
            resolve(typeof result === 'string' ? result : '')
          }
          reader.onerror = reject
          reader.readAsDataURL(file)
        })
        const res = await api.post<UnifiedSearchResponse>('/search/unified', {
          image_base64: base64,
          knowledge_base_id: selectedKbId ?? null,
          top_k: 30,
        })
        setUnifiedResults(res.items || [])
        if (!(res.items?.length)) message.info('未找到相关内容')
      } else {
        const res = await api.post<UnifiedSearchResponse>('/search/unified', {
          query: q,
          knowledge_base_id: selectedKbId ?? null,
          top_k: 30,
        })
        setUnifiedResults(res.items || [])
        if (!(res.items?.length)) message.info('未找到相关内容')
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '检索失败')
    } finally {
      setLoading(false)
    }
  }

  const kbOptions = [
    { value: undefined as number | undefined, label: '全部知识库' },
    ...knowledgeBases.map((kb) => ({ value: kb.id, label: `${kb.name}（${kb.chunk_count ?? 0} 块）` })),
  ]

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>多模态检索</h1>
      <Card style={{ marginBottom: 24 }}>
        <Tabs
          activeKey={tab}
          onChange={(k) => setTab(k as TabMode)}
          items={[
            {
              key: 'text',
              label: '以文搜图',
              children: null,
            },
            {
              key: 'image',
              label: '图搜图',
              children: null,
            },
            {
              key: 'unified',
              label: '统一检索',
              children: null,
            },
          ]}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-start', marginTop: 12 }}>
          {tab !== 'image' && (
            <Input
              placeholder={tab === 'unified' ? '输入关键词或上传图片（二选一）' : '输入描述或关键词，如：生日蛋糕、海边日落'}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onPressEnter={tab === 'text' ? handleTextSearch : handleUnifiedSearch}
              style={{ width: 320 }}
              allowClear
            />
          )}
          {(tab === 'image' || tab === 'unified') && (
            <Upload
              accept="image/jpeg,image/png,image/webp,image/gif"
              maxCount={1}
              fileList={imageFiles}
              onChange={({ fileList }) => setImageFiles(fileList)}
              beforeUpload={() => false}
              showUploadList={true}
            >
              <Button icon={<UploadOutlined />}>上传图片</Button>
            </Upload>
          )}
          <Select
            placeholder="选择知识库（可选）"
            allowClear
            style={{ width: 220 }}
            value={selectedKbId}
            onChange={setSelectedKbId}
            options={kbOptions}
          />
          <Button
            type="primary"
            icon={<SearchOutlined />}
            loading={loading}
            onClick={
              tab === 'text'
                ? handleTextSearch
                : tab === 'image'
                  ? handleImageSearch
                  : handleUnifiedSearch
            }
          >
            搜索
          </Button>
        </div>
      </Card>
      <Card>
        {/* 以文搜图 / 图搜图：图片网格 */}
        {(tab === 'text' || tab === 'image') && (
          <>
            {results.length === 0 && !loading && (
              <Empty
                description={
                  tab === 'text'
                    ? '输入关键词并点击搜索，将在知识库中检索匹配的图片'
                    : '上传一张图片，在知识库中检索相似图片'
                }
                style={{ padding: 48 }}
              />
            )}
            {results.length > 0 && (
              <Row gutter={[16, 16]}>
                {results.map((item) => (
                  <Col xs={24} sm={12} md={8} lg={6} key={item.file_id}>
                    <Card
                      size="small"
                      cover={
                        <ImageThumb
                          fileId={item.file_id}
                          alt={item.original_filename}
                          style={{ borderRadius: '4px 4px 0 0' }}
                        />
                      }
                      actions={[
                        <Button
                          key="download"
                          type="link"
                          size="small"
                          onClick={async () => {
                            try {
                              const blob = (await api.get(`/files/${item.file_id}/download`, {
                                responseType: 'blob',
                              })) as Blob
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
                              const msg =
                                data instanceof Blob && data.type?.includes('json')
                                  ? '文件不存在或无权访问'
                                  : err.response?.status === 404
                                    ? '文件不存在'
                                    : '下载失败'
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
                        description={
                          item.snippet ? (
                            <span style={{ fontSize: 12, color: '#666' }}>{item.snippet}</span>
                          ) : item.score != null ? (
                            <span style={{ fontSize: 12, color: '#999' }}>相似度: {(item.score * 100).toFixed(0)}%</span>
                          ) : undefined
                        }
                      />
                    </Card>
                  </Col>
                ))}
              </Row>
            )}
          </>
        )}
        {/* 统一检索：文档+图片混合列表 */}
        {tab === 'unified' && (
          <>
            {unifiedResults.length === 0 && !loading && (
              <Empty
                description="输入关键词或上传图片，同时检索文档与图片"
                style={{ padding: 48 }}
              />
            )}
            {unifiedResults.length > 0 && (
              <Row gutter={[16, 16]}>
                {unifiedResults.map((item) => (
                  <Col xs={24} sm={12} md={8} lg={6} key={`${item.chunk_id}-${item.file_id}`}>
                    <Card
                      size="small"
                      cover={
                        item.is_image ? (
                          <ImageThumb
                            fileId={Number(item.file_id)}
                            alt={item.original_filename ?? ''}
                            style={{ borderRadius: '4px 4px 0 0' }}
                          />
                        ) : (
                          <div
                            style={{
                              padding: 12,
                              minHeight: 80,
                              background: '#fafafa',
                              borderRadius: '4px 4px 0 0',
                              display: 'flex',
                              alignItems: 'center',
                            }}
                          >
                            <FileTextOutlined style={{ marginRight: 8, color: '#999' }} />
                            <span style={{ fontSize: 12, color: '#666', flex: 1 }}>{item.snippet || '无摘要'}</span>
                          </div>
                        )
                      }
                    >
                      <Card.Meta
                        title={item.original_filename}
                        description={
                          <span style={{ fontSize: 12, color: '#999' }}>
                            {item.is_image ? '图片' : '文档'} · 相关度 {(item.score * 100).toFixed(0)}%
                          </span>
                        }
                      />
                    </Card>
                  </Col>
                ))}
              </Row>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
