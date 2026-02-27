import React, { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Input, Button, Card, List, Avatar, message, Select, Drawer, Space, Popconfirm, Collapse, Modal } from 'antd'
import { SendOutlined, UserOutlined, RobotOutlined, MessageOutlined, PlusOutlined, DeleteOutlined, FileTextOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import api, { streamPost } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import PageSkeleton from '../components/PageSkeleton'
import type { KnowledgeBaseListResponse, ConversationItem, ConversationListResponse, MessageItem, SourceItem } from '../types/api'

/** 判断 JSON 是否为 ECharts 常用 option 结构（含 series 或 xAxis/yAxis） */
function isEChartsOption(obj: unknown): obj is Record<string, unknown> {
  if (!obj || typeof obj !== 'object') return false
  const o = obj as Record<string, unknown>
  return Array.isArray(o.series) || (o.xAxis != null && o.yAxis != null) || (o.series != null && typeof o.series === 'object')
}

/** 解析消息内容：拆出 ```json ... ``` 中可渲染为图表的 ECharts option，其余按文本展示 */
function parseContentWithCharts(content: string | undefined): Array<{ type: 'text' | 'chart'; content: string | Record<string, unknown> }> {
  if (!content || typeof content !== 'string') return [{ type: 'text', content: '' }]
  const parts: Array<{ type: 'text' | 'chart'; content: string | Record<string, unknown> }> = []
  const re = /```(\w*)\s*\n([\s\S]*?)```/g
  let lastEnd = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    if (m.index > lastEnd) {
      parts.push({ type: 'text', content: content.slice(lastEnd, m.index) })
    }
    const lang = (m[1] || '').toLowerCase()
    const code = m[2].trim()
    if (lang === 'json' && code) {
      try {
        const parsed = JSON.parse(code) as unknown
        if (isEChartsOption(parsed)) {
          parts.push({ type: 'chart', content: parsed as Record<string, unknown> })
          lastEnd = re.lastIndex
          continue
        }
      } catch {
        // 非合法 JSON 或非 ECharts，当普通代码块当文本展示
      }
    }
    parts.push({ type: 'text', content: m[0] })
    lastEnd = re.lastIndex
  }
  if (lastEnd < content.length) {
    parts.push({ type: 'text', content: content.slice(lastEnd) })
  }
  if (parts.length === 0) {
    parts.push({ type: 'text', content })
  }
  return parts
}

export default function Chat() {
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [inputValue, setInputValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseListResponse['knowledge_bases']>([])
  const [selectedKbId, setSelectedKbId] = useState<number | undefined>(undefined)
  const [conversations, setConversations] = useState<ConversationItem[]>([])
  // 当前会话 ID：同窗口内多次发送均为同一会话；仅「新对话」时置空以开启新会话
  const [currentConvId, setCurrentConvId] = useState<number | null>(null)
  const [conversationDrawerVisible, setConversationDrawerVisible] = useState(false)
  const [pageLoading, setPageLoading] = useState(true)
  const [sourcePreview, setSourcePreview] = useState<SourceItem | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const justSentMessageRef = useRef(false)
  const navigate = useNavigate()

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    api.get<KnowledgeBaseListResponse>('/knowledge-bases?page_size=100')
      .then((res: KnowledgeBaseListResponse) => setKnowledgeBases(res?.knowledge_bases ?? []))
      .catch(() => {})
    loadConversations()
  }, [])

  useEffect(() => {
    if (currentConvId) {
      // 刚刚发送过消息时不要重新加载，避免覆盖本地消息（含置信度等）
      if (justSentMessageRef.current) {
        justSentMessageRef.current = false
        return
      }
      loadConversationMessages(currentConvId)
    } else {
      setMessages([])
    }
  }, [currentConvId])

  const loadConversations = async (skipAutoSelect = false) => {
    try {
      const res = await api.get<ConversationListResponse>('/chat/conversations?page_size=50')
      setConversations(res.conversations || [])
      if (!skipAutoSelect && res.conversations && res.conversations.length > 0 && !currentConvId) {
        setCurrentConvId(res.conversations[0].id)
      }
    } catch {
      setConversations([])
    } finally {
      setPageLoading(false)
    }
  }

  const loadConversationMessages = async (convId: number, merge: boolean = false) => {
    try {
      const res = await api.get<ConversationItem>(`/chat/conversations/${convId}`)
      if (merge) {
        // 合并消息：保留当前消息中不在服务器返回消息中的消息（按时间戳判断）
        setMessages((prev) => {
          const serverMessages = res.messages || []
          const prevMessageIds = new Set(prev.map(m => m.id))
          const newMessages = serverMessages.filter(m => !prevMessageIds.has(m.id))
          return [...prev, ...newMessages].sort((a, b) => 
            new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
          )
        })
      } else {
        setMessages(res.messages || [])
      }
      // 恢复该对话的知识库选择
      if (res.knowledge_base_id) {
        setSelectedKbId(res.knowledge_base_id)
      }
    } catch {
      message.error('加载对话记录失败')
      if (!merge) {
        setMessages([])
      }
    }
  }

  const handleNewConversation = () => {
    setCurrentConvId(null)
    setMessages([])
    setSelectedKbId(undefined)
  }

  const handleSelectConversation = (convId: number) => {
    setCurrentConvId(convId)
    setConversationDrawerVisible(false)
  }

  const handleDeleteConversation = async (convId: number, e?: React.MouseEvent) => {
    e?.stopPropagation()
    try {
      await api.delete(`/chat/conversations/${convId}`)
      message.success('已删除')
      if (currentConvId === convId) {
        handleNewConversation()
      }
      loadConversations()
    } catch {
      message.error('删除失败')
    }
  }

  const handleSend = async () => {
    if (!inputValue.trim()) return

    const userMessage: MessageItem = {
      id: Date.now(),
      role: 'user',
      content: inputValue,
      tokens: 0,
      created_at: new Date().toISOString(),
    }
    setMessages((prev: MessageItem[]) => [...prev, userMessage])
    const messageContent = inputValue
    setInputValue('')
    setLoading(true)

    const tempAssistantId = Date.now() + 1
    setMessages((prev: MessageItem[]) => [
      ...prev,
      {
        id: tempAssistantId,
        role: 'assistant',
        content: '',
        tokens: 0,
        created_at: new Date().toISOString(),
      } as MessageItem,
    ])

    try {
      const { reader } = await streamPost('chat/completions/stream', {
        content: messageContent,
        knowledge_base_id: selectedKbId ?? null,
        conversation_id: currentConvId ?? null,
      })
      const decoder = new TextDecoder()
      let buffer = ''
      let newConvId: number | null = null
      let confidence: number | null = null
      let sources: SourceItem[] = []
      const tokenQueue: string[] = []
      let drainScheduled = false
      const drainTokenQueue = () => {
        drainScheduled = false
        if (tokenQueue.length === 0) return
        const token = tokenQueue.shift()!
        setMessages((prev) =>
          prev.map((m) =>
            m.id === tempAssistantId ? { ...m, content: (m.content || '') + token } : m
          )
        )
        if (tokenQueue.length > 0) {
          drainScheduled = true
          requestAnimationFrame(drainTokenQueue)
        }
      }

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
              const event = JSON.parse(data) as { type: string; content?: string; conversation_id?: number; confidence?: number; sources?: SourceItem[]; tools_used?: string[] }
              if (event.type === 'token' && event.content) {
                tokenQueue.push(event.content)
                if (!drainScheduled) {
                  drainScheduled = true
                  requestAnimationFrame(drainTokenQueue)
                }
              } else if (event.type === 'done') {
                newConvId = event.conversation_id ?? null
                confidence = event.confidence ?? null
                sources = event.sources ?? []
                const toolsUsed = event.tools_used ?? []
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === tempAssistantId
                      ? { ...m, confidence: confidence ?? undefined, sources: sources.length ? sources : undefined, tools_used: toolsUsed.length ? toolsUsed : undefined }
                      : m
                  )
                )
              } else if (event.type === 'error') {
                throw new Error((event as { message?: string }).message || '流式返回错误')
              }
            } catch (e) {
              if (e instanceof SyntaxError) continue
              throw e
            }
          }
        }
      justSentMessageRef.current = true
      if (!currentConvId && newConvId) {
        setCurrentConvId(newConvId)
      }
      try {
        if (currentConvId || newConvId) {
          await loadConversations(true)
        }
      } catch {
        // ignore
      }
    } catch (err: unknown) {
      console.error('发送消息失败:', err)
      const msg = err instanceof Error ? err.message : '发送消息失败'
      message.error(msg)
      setMessages((prev) => prev.filter((m) => m.id !== tempAssistantId))
    } finally {
      setLoading(false)
    }
  }

  const currentConversation = conversations.find((c) => c.id === currentConvId)

  if (pageLoading) return <PageSkeleton rows={5} />

  return (
    <div style={{ height: 'calc(100vh - 200px)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h1 className="app-page-title" style={{ margin: 0, marginRight: 8 }}>智能问答</h1>
        <Space>
          <Button
            type={currentConvId ? 'default' : 'primary'}
            icon={<PlusOutlined />}
            onClick={handleNewConversation}
          >
            新对话
          </Button>
          <Button
            icon={<MessageOutlined />}
            onClick={() => setConversationDrawerVisible(true)}
          >
            {currentConversation ? currentConversation.title || '当前对话' : '选择对话'}
          </Button>
          <Select
            placeholder="选择知识库（可选）"
            allowClear
            style={{ width: 200 }}
            value={selectedKbId}
            onChange={setSelectedKbId}
            disabled={!!currentConvId}
            options={[
              { value: undefined, label: '不限定知识库' },
              ...knowledgeBases.map((kb: KnowledgeBaseListResponse['knowledge_bases'][0]) => ({ value: kb.id, label: `${kb.name}（${kb.chunk_count || 0} 块）` })),
            ]}
          />
        </Space>
      </div>
      <Card 
        style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        bodyStyle={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '16px' }}
      >
        {currentConvId && currentConversation && (
          <div style={{ marginBottom: 12, paddingBottom: 12, borderBottom: '1px solid var(--app-border-subtle)', flexShrink: 0 }}>
            <Space>
              <span style={{ fontWeight: 500, color: 'var(--app-text-primary)' }}>{currentConversation.title || '未命名对话'}</span>
              <span style={{ color: 'var(--app-text-muted)', fontSize: 12 }}>
                {new Date(currentConversation.updated_at).toLocaleString('zh-CN')}
              </span>
            </Space>
          </div>
        )}
        <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', marginBottom: 16, minHeight: 0, WebkitOverflowScrolling: 'touch' }}>
          {messages.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--app-text-muted)' }}>
              暂无消息，开始对话吧
            </div>
          ) : (
            <List
              dataSource={messages}
              renderItem={(item: MessageItem) => (
                <List.Item style={{ border: 'none', padding: '8px 0' }}>
                  <List.Item.Meta
                    avatar={
                      <Avatar
                        icon={item.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                        style={{
                          backgroundColor: item.role === 'user' ? '#1890ff' : '#52c41a',
                        }}
                      />
                    }
                    title={
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span>{item.role === 'user' ? '我' : 'AI助手'}</span>
                        {item.confidence !== undefined && item.confidence !== null && (
                          <span style={{ 
                            fontSize: 12, 
                            color: item.confidence < 0.6 ? '#ff4d4f' : '#52c41a',
                            backgroundColor: item.confidence < 0.6 ? 'var(--app-error-bg)' : 'var(--app-success-bg)',
                            padding: '2px 8px',
                            borderRadius: 4,
                            border: item.confidence < 0.6 ? '1px solid rgba(255,77,79,0.4)' : '1px solid rgba(82,196,26,0.4)',
                            color: 'var(--app-text-primary)'
                          }}>
                            置信度: {(item.confidence * 100).toFixed(1)}% {item.confidence < 0.6 ? '(低)' : ''}
                          </span>
                        )}
                      </div>
                    }
                    description={
                      <div>
                        <div style={{ marginBottom: (item.max_confidence_context || item.retrieved_context) ? 12 : 0 }}>
                          {parseContentWithCharts(item.content).map((part, idx) =>
                            part.type === 'text' ? (
                              <div key={idx} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--app-text-primary)' }}>
                                {part.content as string}
                              </div>
                            ) : (
                              <div key={idx} style={{ width: '100%', minHeight: 280, marginTop: 8, marginBottom: 8 }}>
                                <ReactECharts option={part.content as Record<string, unknown>} style={{ height: 320 }} notMerge />
                              </div>
                            )
                          )}
                        </div>
                        {/* 本回复调用的 MCP 工具 */}
                        {item.role === 'assistant' && item.tools_used && item.tools_used.length > 0 && (
                          <div style={{ marginTop: 8, marginBottom: 4, fontSize: 12, color: 'var(--app-text-muted)' }}>
                            <span style={{ color: '#1890ff', fontWeight: 500 }}>调用了以下工具：</span>{' '}
                            {item.tools_used.join('、')}
                          </div>
                        )}
                        {/* 有参考来源时以溯源为主；无 sources 时再显示最高置信度上下文（兼容旧数据） */}
                        {item.max_confidence_context && !(item.sources && item.sources.length > 0) && (
                          <div style={{
                            marginTop: 12,
                            padding: 12,
                            backgroundColor: 'var(--app-info-bg)',
                            border: '1px solid var(--app-border-info)',
                            borderRadius: 4,
                            maxHeight: '300px',
                            overflowY: 'auto',
                            overflowX: 'hidden',
                            fontSize: 12,
                            color: 'var(--app-text-muted)',
                            WebkitOverflowScrolling: 'touch'
                          }}>
                            <div style={{ fontWeight: 500, marginBottom: 8, color: '#1890ff', flexShrink: 0 }}>
                              最高置信度上下文
                              {item.confidence !== undefined && item.confidence !== null && (
                                <>（置信度: {(item.confidence * 100).toFixed(1)}%）</>
                              )}
                              ：
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--app-text-primary)' }}>
                              {item.max_confidence_context}
                            </div>
                          </div>
                        )}
                        {/* 显示所有检索到的上下文（仅在低置信度时显示） */}
                        {item.retrieved_context && item.confidence !== undefined && item.confidence !== null && item.confidence < 0.6 && (
                          <div style={{
                            marginTop: 12,
                            padding: 12,
                            backgroundColor: 'var(--app-bg-subtle)',
                            border: '1px solid #d9d9d9',
                            borderRadius: 4,
                            maxHeight: '300px',
                            overflowY: 'auto',
                            overflowX: 'hidden',
                            fontSize: 12,
                            color: 'var(--app-text-muted)',
                            WebkitOverflowScrolling: 'touch'
                          }}>
                            <div style={{ fontWeight: 500, marginBottom: 8, color: 'var(--app-text-primary)', flexShrink: 0 }}>
                              所有检索到的上下文（置信度: {(item.confidence * 100).toFixed(1)}%）：
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                              {item.retrieved_context}
                            </div>
                          </div>
                        )}
                        {/* 引用与溯源：参考来源 */}
                        {item.sources && item.sources.length > 0 && (
                          <Collapse
                            size="small"
                            style={{ marginTop: 12 }}
                            items={[
                              {
                                key: 'sources',
                                label: (
                                  <span style={{ fontSize: 12, color: 'var(--app-accent)' }}>
                                    <FileTextOutlined /> 参考来源（{item.sources.length} 条）
                                  </span>
                                ),
                                children: (
                                  <div style={{ fontSize: 12, color: 'var(--app-text-muted)' }}>
                                    {item.sources.map((s: SourceItem, i: number) => (
                                      <div
                                        key={`${s.file_id}-${s.chunk_index}-${i}`}
                                        style={{
                                          marginBottom: 8,
                                          padding: 8,
                                          backgroundColor: 'var(--app-bg-muted)',
                                          borderRadius: 4,
                                          borderLeft: '3px solid var(--app-accent)',
                                        }}
                                      >
                                        <div style={{ fontWeight: 500, marginBottom: 4, color: 'var(--app-text-primary)' }}>
                                          {s.original_filename} · 第 {s.chunk_index + 1} 段
                                        </div>
                                        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--app-text-primary)' }}>
                                          {s.snippet}
                                          {s.snippet.length >= 200 ? '…' : ''}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ),
                              },
                            ]}
                          />
                        )}
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
          )}
          <div ref={messagesEndRef} />
        </div>
        <div style={{ flexShrink: 0, marginTop: 'auto' }}>
          <Input.Group compact>
            <Input
              value={inputValue}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputValue(e.target.value)}
              onPressEnter={handleSend}
              placeholder="输入您的问题..."
              style={{ width: 'calc(100% - 80px)' }}
              size="large"
              disabled={loading}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              loading={loading}
              size="large"
              disabled={loading}
            >
              发送
            </Button>
          </Input.Group>
        </div>
      </Card>

      <Modal
        title={sourcePreview ? `${sourcePreview.original_filename} · 第 ${(sourcePreview.chunk_index ?? 0) + 1} 段` : '引用来源'}
        open={!!sourcePreview}
        onCancel={() => setSourcePreview(null)}
        footer={
          sourcePreview ? (
            <Space>
              {sourcePreview.knowledge_base_id != null && (
                <Button type="primary" onClick={() => { navigate('/knowledge-bases'); setSourcePreview(null); }}>
                  在知识库中查看
                </Button>
              )}
              <Button onClick={() => { navigate('/files'); setSourcePreview(null); }}>
                在文件管理中查看
              </Button>
            </Space>
          ) : null
        }
      >
        {sourcePreview && (
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 320, overflow: 'auto' }}>
            {sourcePreview.snippet}
            {sourcePreview.snippet.length >= 200 ? '…' : ''}
          </div>
        )}
      </Modal>

      <Drawer
        title="对话历史"
        placement="left"
        width={320}
        open={conversationDrawerVisible}
        onClose={() => setConversationDrawerVisible(false)}
      >
        <List
          dataSource={conversations}
          renderItem={(conv: ConversationItem) => (
            <List.Item
              style={{
                cursor: 'pointer',
                backgroundColor: currentConvId === conv.id ? 'var(--app-list-active)' : 'transparent',
                padding: '12px',
                borderRadius: 4,
                marginBottom: 8,
              }}
              onClick={() => handleSelectConversation(conv.id)}
              actions={[
                <Popconfirm
                  title="确定删除该对话？"
                  onConfirm={(e) => handleDeleteConversation(conv.id, e)}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e: React.MouseEvent) => e.stopPropagation()}
                  />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={<div style={{ fontWeight: currentConvId === conv.id ? 600 : 400 }}>{conv.title || '未命名对话'}</div>}
                description={
                  <div style={{ fontSize: 12, color: 'var(--app-text-muted)' }}>
                    {new Date(conv.updated_at).toLocaleString('zh-CN')}
                  </div>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
    </div>
  )
}
