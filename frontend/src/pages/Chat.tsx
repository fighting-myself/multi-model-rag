import React, { useState, useRef, useEffect } from 'react'
import { Input, Button, Card, List, Avatar, message, Select, Drawer, Space, Popconfirm } from 'antd'
import { SendOutlined, UserOutlined, RobotOutlined, MessageOutlined, PlusOutlined, DeleteOutlined } from '@ant-design/icons'
import api from '../services/api'
import type { KnowledgeBaseListResponse, ChatCompletionResponse, ConversationItem, ConversationListResponse, MessageItem } from '../types/api'

export default function Chat() {
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [inputValue, setInputValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseListResponse['knowledge_bases']>([])
  const [selectedKbId, setSelectedKbId] = useState<number | undefined>(undefined)
  const [conversations, setConversations] = useState<ConversationItem[]>([])
  const [currentConvId, setCurrentConvId] = useState<number | null>(null)
  const [conversationDrawerVisible, setConversationDrawerVisible] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const justSentMessageRef = useRef(false)  // 标记是否刚刚发送了消息

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
      // 只有在切换对话时才加载消息，避免覆盖刚刚发送的消息
      loadConversationMessages(currentConvId)
    } else {
      setMessages([])
    }
  }, [currentConvId])

  const loadConversations = async () => {
    try {
      const res = await api.get<ConversationListResponse>('/chat/conversations?page_size=50')
      setConversations(res.conversations || [])
      // 自动加载最新对话
      if (res.conversations && res.conversations.length > 0 && !currentConvId) {
        setCurrentConvId(res.conversations[0].id)
      }
    } catch {
      setConversations([])
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

    try {
      const payload: { content: string; knowledge_base_id?: number; conversation_id?: number } = {
        content: messageContent,
      }
      if (selectedKbId) payload.knowledge_base_id = selectedKbId
      if (currentConvId) payload.conversation_id = currentConvId
      const response = await api.post<ChatCompletionResponse>('/chat/completions', payload)
      
      const assistantMessage: MessageItem = {
        id: Date.now() + 1,
        role: 'assistant',
        content: response.message,
        tokens: response.tokens,
        model: response.model,
        created_at: response.created_at,
        confidence: response.confidence,
        retrieved_context: response.retrieved_context,
        max_confidence_context: response.max_confidence_context,
      }
      setMessages((prev: MessageItem[]) => [...prev, assistantMessage])
      
      // 标记刚刚发送了消息
      justSentMessageRef.current = true
      
      // 更新当前对话 ID（新对话时），但不触发重新加载消息
      if (!currentConvId && response.conversation_id) {
        setCurrentConvId(response.conversation_id)
      }
      
      // 刷新对话列表（更新标题和时间），但不重新加载消息（避免覆盖置信度字段）
      // 注意：这里只刷新对话列表，不会触发 useEffect 重新加载消息（因为 justSentMessageRef 标记）
      if (currentConvId || response.conversation_id) {
        loadConversations()
      }
    } catch {
      message.error('发送消息失败')
      // 移除刚添加的用户消息（发送失败时）
      setMessages((prev) => prev.slice(0, -1))
    } finally {
      setLoading(false)
    }
  }

  const currentConversation = conversations.find((c) => c.id === currentConvId)

  return (
    <div style={{ height: 'calc(100vh - 200px)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0 }}>智能问答</h1>
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
          <div style={{ marginBottom: 12, paddingBottom: 12, borderBottom: '1px solid #f0f0f0', flexShrink: 0 }}>
            <Space>
              <span style={{ fontWeight: 500 }}>{currentConversation.title || '未命名对话'}</span>
              <span style={{ color: '#999', fontSize: 12 }}>
                {new Date(currentConversation.updated_at).toLocaleString('zh-CN')}
              </span>
            </Space>
          </div>
        )}
        <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', marginBottom: 16, minHeight: 0, WebkitOverflowScrolling: 'touch' }}>
          {messages.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
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
                            backgroundColor: item.confidence < 0.6 ? '#fff1f0' : '#f6ffed',
                            padding: '2px 8px',
                            borderRadius: 4,
                            border: item.confidence < 0.6 ? '1px solid #ffccc7' : '1px solid #b7eb8f'
                          }}>
                            置信度: {(item.confidence * 100).toFixed(1)}% {item.confidence < 0.6 ? '(低)' : ''}
                          </span>
                        )}
                      </div>
                    }
                    description={
                      <div>
                        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: (item.max_confidence_context || item.retrieved_context) ? 12 : 0 }}>
                          {item.content}
                        </div>
                        {/* 显示最高置信度对应的上下文（总是显示，如果有） */}
                        {item.max_confidence_context && (
                          <div style={{
                            marginTop: 12,
                            padding: 12,
                            backgroundColor: '#f0f9ff',
                            border: '1px solid #91d5ff',
                            borderRadius: 4,
                            maxHeight: '300px',
                            overflowY: 'auto',
                            overflowX: 'hidden',
                            fontSize: 12,
                            color: '#666',
                            WebkitOverflowScrolling: 'touch'
                          }}>
                            <div style={{ fontWeight: 500, marginBottom: 8, color: '#1890ff', flexShrink: 0 }}>
                              最高置信度上下文
                              {item.confidence !== undefined && item.confidence !== null && (
                                <>（置信度: {(item.confidence * 100).toFixed(1)}%）</>
                              )}
                              ：
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                              {item.max_confidence_context}
                            </div>
                          </div>
                        )}
                        {/* 显示所有检索到的上下文（仅在低置信度时显示） */}
                        {item.retrieved_context && item.confidence !== undefined && item.confidence !== null && item.confidence < 0.6 && (
                          <div style={{
                            marginTop: 12,
                            padding: 12,
                            backgroundColor: '#fafafa',
                            border: '1px solid #d9d9d9',
                            borderRadius: 4,
                            maxHeight: '300px',
                            overflowY: 'auto',
                            overflowX: 'hidden',
                            fontSize: 12,
                            color: '#666',
                            WebkitOverflowScrolling: 'touch'
                          }}>
                            <div style={{ fontWeight: 500, marginBottom: 8, color: '#333', flexShrink: 0 }}>
                              所有检索到的上下文（置信度: {(item.confidence * 100).toFixed(1)}%）：
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                              {item.retrieved_context}
                            </div>
                          </div>
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
                backgroundColor: currentConvId === conv.id ? '#e6f7ff' : 'transparent',
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
                  <div style={{ fontSize: 12, color: '#999' }}>
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
