import { useState, useEffect, useRef } from 'react'
import { Card, Input, Button, Select, Row, Col, message, Empty, Tabs, Upload, Modal, Drawer, Spin } from 'antd'
import { SearchOutlined, PictureOutlined, UploadOutlined, FileTextOutlined, EyeOutlined, DownloadOutlined, FileSearchOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd/es/upload/interface'
import api from '../services/api'
import PageSkeleton from '../components/PageSkeleton'
import type {
  KnowledgeBaseListResponse,
  ImageSearchItem,
  ImageSearchResponse,
  UnifiedSearchItem,
  UnifiedSearchResponse,
  ChunkListResponse,
  ChunkItem,
} from '../types/api'

/** 检索结果卡片统一尺寸 */
const RESULT_CARD_COVER_HEIGHT = 140
const RESULT_CARD_BODY_MIN_HEIGHT = 88

/** 在原文内容中高亮片段（仅首处匹配） */
function HighlightSnippet({ content, snippet }: { content: string; snippet: string }) {
  if (!snippet || !content.includes(snippet)) return <>{content}</>
  const idx = content.indexOf(snippet)
  return (
    <>
      {content.slice(0, idx)}
      <mark style={{ background: 'rgba(255, 235, 59, 0.6)', padding: '0 2px', borderRadius: 2 }}>{snippet}</mark>
      {content.slice(idx + snippet.length)}
    </>
  )
}

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
          background: 'var(--app-bg-muted)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          ...style,
        }}
      >
        <PictureOutlined style={{ fontSize: 32, color: 'var(--app-icon-muted)' }} />
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
  const [selectedKbIds, setSelectedKbIds] = useState<number[]>([])
  const [loading, setLoading] = useState(false)
  const [initLoading, setInitLoading] = useState(true)
  const [imageFiles, setImageFiles] = useState<UploadFile[]>([])
  const [results, setResults] = useState<ImageSearchItem[]>([])
  const [unifiedResults, setUnifiedResults] = useState<UnifiedSearchItem[]>([])
  const [snippetModal, setSnippetModal] = useState<{ visible: boolean; title: string; snippet: string }>({ visible: false, title: '', snippet: '' })
  const [sourceDrawer, setSourceDrawer] = useState<{
    visible: boolean
    title: string
    fileId: number | null
    kbId: number | null
    chunkId: number | null
    snippet: string
    chunks: ChunkItem[]
    loading: boolean
  }>({ visible: false, title: '', fileId: null, kbId: null, chunkId: null, snippet: '', chunks: [], loading: false })

  const openSourceDrawer = (item: UnifiedSearchItem) => {
    const kbId = item.knowledge_base_id ?? null
    if (kbId == null) {
      message.warning('该结果无法定位知识库，请指定知识库后重新检索')
      return
    }
    setSourceDrawer({
      visible: true,
      title: item.original_filename ?? '',
      fileId: item.file_id,
      kbId,
      chunkId: item.chunk_id,
      snippet: item.snippet || '',
      chunks: [],
      loading: true,
    })
    api
      .get<ChunkListResponse>(`/knowledge-bases/${kbId}/files/${item.file_id}/chunks`)
      .then((res) => {
        setSourceDrawer((s) => ({ ...s, chunks: res.chunks || [], loading: false }))
      })
      .catch(() => {
        message.error('获取原文分块失败')
        setSourceDrawer((s) => ({ ...s, loading: false }))
      })
  }

  const handleDownloadFile = async (fileId: number, filename: string) => {
    try {
      const blob = (await api.get(`/files/${fileId}/download`, { responseType: 'blob' })) as Blob
      if (blob.type.startsWith('application/json') || blob.size === 0) {
        message.error('文件不存在或无法下载')
        return
      }
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename || `file-${fileId}`
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
  }

  useEffect(() => {
    setInitLoading(true)
    api
      .get<KnowledgeBaseListResponse>('/knowledge-bases')
      .then((res) => setKnowledgeBases(res.knowledge_bases || []))
      .catch(() => {})
      .finally(() => setInitLoading(false))
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
        knowledge_base_ids: selectedKbIds.length ? selectedKbIds : null,
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
          knowledge_base_ids: selectedKbIds.length ? selectedKbIds : null,
          top_k: 30,
        })
        setUnifiedResults(res.items || [])
        if (!(res.items?.length)) message.info('未找到相关内容')
      } else {
        const res = await api.post<UnifiedSearchResponse>('/search/unified', {
          query: q,
          knowledge_base_ids: selectedKbIds.length ? selectedKbIds : null,
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

  if (initLoading) return <PageSkeleton rows={4} />

  return (
    <div className="app-page">
      <div className="app-page-header">
        <h1 className="app-page-title">多模态检索</h1>
        <p className="app-page-desc">以文搜图、图搜图或统一检索文档与图片</p>
      </div>
      <Card className="app-page-section">
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
            mode="multiple"
            placeholder="选择知识库（可多选，不选则检索全部）"
            allowClear
            style={{ width: 280 }}
            value={selectedKbIds}
            onChange={setSelectedKbIds}
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
      <Card className="app-page-section">
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
                      style={{ height: '100%', minHeight: RESULT_CARD_COVER_HEIGHT + RESULT_CARD_BODY_MIN_HEIGHT }}
                      bodyStyle={{ minHeight: RESULT_CARD_BODY_MIN_HEIGHT }}
                      cover={
                        <div style={{ height: RESULT_CARD_COVER_HEIGHT, overflow: 'hidden', background: 'var(--app-bg-muted)', borderRadius: '4px 4px 0 0' }}>
                          <ImageThumb
                            fileId={item.file_id}
                            alt={item.original_filename}
                            style={{ borderRadius: '4px 4px 0 0', height: RESULT_CARD_COVER_HEIGHT, width: '100%', objectFit: 'cover' }}
                          />
                        </div>
                      }
                      actions={[
                        ...(item.snippet
                          ? [
                              <Button
                                key="snippet"
                                type="link"
                                size="small"
                                icon={<EyeOutlined />}
                                onClick={() => setSnippetModal({ visible: true, title: item.original_filename, snippet: item.snippet })}
                              >
                                查看片段
                              </Button>,
                            ]
                          : []),
                        <Button
                          key="download"
                          type="link"
                          size="small"
                          icon={<DownloadOutlined />}
                          onClick={() => handleDownloadFile(item.file_id, item.original_filename || `file-${item.file_id}`)}
                        >
                          下载原文
                        </Button>,
                      ]}
                    >
                      <Card.Meta
                        title={<span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.original_filename}>{item.original_filename}</span>}
                        description={
                          <span style={{ fontSize: 12, color: 'var(--app-text-muted)' }}>
                            {item.snippet ? (
                              <span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{item.snippet}</span>
                            ) : item.score != null ? (
                              `相似度 ${(item.score * 100).toFixed(0)}%`
                            ) : (
                              '—'
                            )}
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
                      style={{ height: '100%', minHeight: RESULT_CARD_COVER_HEIGHT + RESULT_CARD_BODY_MIN_HEIGHT }}
                      bodyStyle={{ minHeight: RESULT_CARD_BODY_MIN_HEIGHT }}
                      cover={
                        <div style={{ height: RESULT_CARD_COVER_HEIGHT, overflow: 'hidden', background: 'var(--app-bg-muted)', borderRadius: '4px 4px 0 0' }}>
                          {item.is_image ? (
                            <ImageThumb
                              fileId={Number(item.file_id)}
                              alt={item.original_filename ?? ''}
                              style={{ borderRadius: '4px 4px 0 0', height: RESULT_CARD_COVER_HEIGHT, width: '100%', objectFit: 'cover' }}
                            />
                          ) : (
                            <div
                              style={{
                                padding: 12,
                                height: RESULT_CARD_COVER_HEIGHT,
                                background: '#fafafa',
                                borderRadius: '4px 4px 0 0',
                                display: 'flex',
                                alignItems: 'flex-start',
                                overflow: 'hidden',
                              }}
                            >
                              <FileTextOutlined style={{ marginRight: 8, marginTop: 2, color: 'var(--app-text-muted)', flexShrink: 0 }} />
                              <span
                                style={{
                                  fontSize: 12,
                                  color: 'var(--app-text-muted)',
                                  flex: 1,
                                  display: '-webkit-box',
                                  WebkitLineClamp: 5,
                                  WebkitBoxOrient: 'vertical',
                                  overflow: 'hidden',
                                }}
                              >
                                {item.snippet || '无摘要'}
                              </span>
                            </div>
                          )}
                        </div>
                      }
                      actions={[
                        <Button
                          key="snippet"
                          type="link"
                          size="small"
                          icon={<EyeOutlined />}
                          onClick={() => setSnippetModal({ visible: true, title: item.original_filename ?? '', snippet: item.snippet || '无摘要' })}
                        >
                          查看片段
                        </Button>,
                        ...(item.knowledge_base_id != null
                          ? [
                              <Button
                                key="source"
                                type="link"
                                size="small"
                                icon={<FileSearchOutlined />}
                                onClick={() => openSourceDrawer(item)}
                              >
                                原文查看
                              </Button>,
                            ]
                          : []),
                        <Button
                          key="download"
                          type="link"
                          size="small"
                          icon={<DownloadOutlined />}
                          onClick={() => handleDownloadFile(Number(item.file_id), item.original_filename || '')}
                        >
                          下载原文
                        </Button>,
                      ]}
                    >
                      <Card.Meta
                        title={<span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.original_filename}>{item.original_filename}</span>}
                        description={
                          <span style={{ fontSize: 12, color: 'var(--app-text-muted)' }}>
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
      <Drawer
        title={sourceDrawer.title ? `原文 · ${sourceDrawer.title}` : '原文查看'}
        open={sourceDrawer.visible}
        onClose={() => setSourceDrawer((s) => ({ ...s, visible: false }))}
        width={480}
        destroyOnClose
        styles={{ body: { paddingTop: 16 } }}
      >
        {sourceDrawer.loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
            <Spin tip="加载原文分块…" />
          </div>
        ) : sourceDrawer.chunks.length === 0 ? (
          <Empty description="暂无分块内容" style={{ marginTop: 48 }} />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {sourceDrawer.chunks.map((c) => {
              const isHighlightChunk = c.id === sourceDrawer.chunkId
              return (
                <div
                  key={c.id}
                  style={{
                    padding: 12,
                    borderRadius: 8,
                    background: isHighlightChunk ? 'rgba(255, 235, 59, 0.15)' : 'var(--app-bg-muted)',
                    border: isHighlightChunk ? '1px solid rgba(255, 193, 7, 0.6)' : '1px solid transparent',
                  }}
                >
                  <div style={{ fontSize: 12, color: 'var(--app-text-muted)', marginBottom: 6 }}>
                    第 {c.chunk_index + 1} 段
                    {isHighlightChunk && <span style={{ marginLeft: 8, color: 'var(--app-accent)' }}>· 命中片段</span>}
                  </div>
                  <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.6 }}>
                    <HighlightSnippet content={c.content || ''} snippet={sourceDrawer.snippet} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Drawer>
      <Modal
        title={snippetModal.title ? `片段 · ${snippetModal.title}` : '查看片段'}
        open={snippetModal.visible}
        onCancel={() => setSnippetModal((s) => ({ ...s, visible: false }))}
        footer={
          <Button type="primary" onClick={() => setSnippetModal((s) => ({ ...s, visible: false }))}>
            关闭
          </Button>
        }
        width={560}
        destroyOnClose
      >
        <div style={{ maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word', padding: '8px 0', color: 'var(--app-text)' }}>
          {snippetModal.snippet || '无内容'}
        </div>
      </Modal>
    </div>
  )
}
