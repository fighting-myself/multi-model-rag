import React, { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Input, Button, Card, List, message, Select, Drawer, Space, Popconfirm, Collapse, Modal, Switch } from 'antd'
import { StopOutlined, MessageOutlined, PlusOutlined, DeleteOutlined, FileTextOutlined, PictureOutlined, VideoCameraOutlined, GlobalOutlined, PaperClipOutlined, CloseOutlined, LoadingOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import api, { streamPost, uploadChatFile } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import PageSkeleton from '../components/PageSkeleton'
import type { KnowledgeBaseListResponse, ConversationItem, ConversationListResponse, MessageItem, MessageAttachmentDisplay, SourceItem, WebSourceItem } from '../types/api'

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

/** 豆包式时长：不足 60 秒用「12s」，否则「1m 24s」 */
function formatThinkingDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0s'
  if (sec < 60) return `${Math.max(0, Math.round(sec))}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m ${s}s`
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(new Error('读取失败'))
    r.readAsDataURL(file)
  })
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => {
      const dataUrl = r.result as string
      const base64 = dataUrl.indexOf(',') >= 0 ? dataUrl.slice(dataUrl.indexOf(',') + 1) : ''
      resolve(base64)
    }
    r.onerror = () => reject(new Error('读取失败'))
    r.readAsDataURL(file)
  })
}

export interface ChatAttachmentConfig {
  max_count: number
  max_size_bytes: number
  image_types: string[]
  file_extensions: string[]
}

/** 对话内容与输入框统一的横向最大宽度（与 AI/用户气泡左右对齐） */
const CHAT_CONTENT_MAX_WIDTH = 1024

type AgentTraceItem = { step?: string; title?: string; text?: string; data?: unknown }

function mergeAgentTrace(items: AgentTraceItem[]): AgentTraceItem[] {
  const out: AgentTraceItem[] = []
  for (const cur of items || []) {
    const text = (cur.text || '').trim()
    const title = (cur.title || '').trim()
    const step = (cur.step || '').trim()
    if (!text && !title) continue
    const prev = out[out.length - 1]
    // 连续同阶段同标题时合并为一个块，减少“重复标题刷屏”
    if (prev && (prev.title || '') === title && (prev.step || '') === step) {
      const prevText = (prev.text || '').trim()
      const nextText = text || title
      const append = nextText && nextText !== prevText
      out[out.length - 1] = {
        ...prev,
        text: append ? `${prevText}\n${nextText}`.trim() : prevText,
      }
      continue
    }
    out.push({ ...cur, text: text || title })
  }
  return out
}

export default function Chat() {
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [inputValue, setInputValue] = useState('')
  const [attachmentConfig, setAttachmentConfig] = useState<ChatAttachmentConfig | null>(null)
  const [attachmentList, setAttachmentList] = useState<Array<{ id: string; file: File; dataUrl?: string; isImage: boolean; isVideo: boolean; fileName: string; uploadId?: string }>>([])
  const [loading, setLoading] = useState(false)
  // 每条 assistant 消息的思考面板展开状态
  const [thinkingOpenMap, setThinkingOpenMap] = useState<Record<number, boolean>>({})
  /** 当前正在流式生成中的助手消息 id（用于思考区标题「思考中」与计时） */
  const [streamingAssistantId, setStreamingAssistantId] = useState<number | null>(null)
  /** 思考阶段已过秒数（仅当 loading 且存在流式助手消息时递增） */
  const [thinkingLiveSec, setThinkingLiveSec] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const dragCounterRef = useRef(0)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const tokenRafRef = useRef<number | null>(null)
  const traceRafRef = useRef<number | null>(null)
  const scrollRafRef = useRef<number | null>(null)
  const isMountedRef = useRef(true)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseListResponse['knowledge_bases']>([])
  const [selectedKbIds, setSelectedKbIds] = useState<number[]>([])
  const [conversations, setConversations] = useState<ConversationItem[]>([])
  /** 豆包式：图片点击放大 */
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null)
  /** 豆包式：文件点击在侧边栏查看（历史会话无文件内容，仅展示说明） */
  const [fileDrawerVisible, setFileDrawerVisible] = useState(false)
  const [fileDrawerTitle, setFileDrawerTitle] = useState('')
  const [fileDrawerContent, setFileDrawerContent] = useState<string>('')
  // 当前会话 ID：同窗口内多次发送均为同一会话；仅「新对话」时置空以开启新会话
  const [currentConvId, setCurrentConvId] = useState<number | null>(null)
  const [conversationDrawerVisible, setConversationDrawerVisible] = useState(false)
  const [pageLoading, setPageLoading] = useState(true)
  const [sourcePreview, setSourcePreview] = useState<SourceItem | null>(null)
  /** 关闭：普通问答；开启：超能模式（内部 RAG → MCP → Skills 依次补上下文） */
  const [superMode, setSuperMode] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  /** 用户是否停留在底部附近；为 false 时流式输出不再强制滚到底，便于向上翻阅 */
  const stickToBottomRef = useRef(true)
  const justSentMessageRef = useRef(false)
  const navigate = useNavigate()

  const scrollToBottom = () => {
    // 流式过程/思考逐字会高频触发 messages 更新，smooth 滚动会造成明显卡顿
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
  }

  useEffect(() => {
    return () => {
      isMountedRef.current = false
      // 离开 Chat 页面时，强制中断 SSE，避免后台 reader/动画继续占用主线程
      abortControllerRef.current?.abort()
      if (tokenRafRef.current != null) {
        cancelAnimationFrame(tokenRafRef.current)
        tokenRafRef.current = null
      }
      if (traceRafRef.current != null) {
        cancelAnimationFrame(traceRafRef.current)
        traceRafRef.current = null
      }
      if (scrollRafRef.current != null) {
        cancelAnimationFrame(scrollRafRef.current)
        scrollRafRef.current = null
      }
    }
  }, [])

  // 监听主内容区滚动：用户离开底部后不再自动跟随流式输出
  useEffect(() => {
    if (pageLoading) return
    const end = messagesEndRef.current
    const parent = end?.closest('.app-content-area') as HTMLElement | null
    if (!parent) return
    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = parent
      const gap = scrollHeight - scrollTop - clientHeight
      stickToBottomRef.current = gap < 120
    }
    parent.addEventListener('scroll', onScroll, { passive: true })
    // 切勿在挂载时同步调用 onScroll()：会覆盖 handleSend 里设的「贴底」，
    // 若发送前不在底部则 gap 大 → 误判为不贴底 → 流式阶段不再滚到底，界面像「没输出」。
    return () => parent.removeEventListener('scroll', onScroll)
  }, [pageLoading, messages.length])

  // 高频更新时最多每帧滚动一次；仅当用户仍在底部附近时才跟随（流式时可自由向上滚动）
  useEffect(() => {
    if (!stickToBottomRef.current) return
    if (scrollRafRef.current != null) return
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null
      scrollToBottom()
    })
  }, [messages])

  useEffect(() => {
    if (!loading || streamingAssistantId == null) {
      setThinkingLiveSec(0)
      return
    }
    setThinkingLiveSec(0)
    const id = window.setInterval(() => setThinkingLiveSec((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [loading, streamingAssistantId])

  useEffect(() => {
    api.get<KnowledgeBaseListResponse>('/knowledge-bases?page_size=100')
      .then((res: KnowledgeBaseListResponse) => setKnowledgeBases(res?.knowledge_bases ?? []))
      .catch(() => {})
    api.get<ChatAttachmentConfig>('/chat/settings/chat-attachment')
      .then((res: ChatAttachmentConfig) => setAttachmentConfig(res))
      .catch(() => setAttachmentConfig({ max_count: 10, max_size_bytes: 20 * 1024 * 1024, image_types: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'], file_extensions: ['pdf', 'doc', 'docx', 'txt', 'xlsx', 'xls', 'pptx', 'ppt', 'md'] }))
    // 防止后端/代理挂起导致永远不结束 loading（如 Redis 阻塞时）
    let safetyCleared = false
    const safety = window.setTimeout(() => {
      safetyCleared = true
      setPageLoading(false)
    }, 15000)
    loadConversations().finally(() => {
      if (!safetyCleared) window.clearTimeout(safety)
    })
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
        // 服务端不落库 agent_trace，重拉会话时用本地同 id 消息合并，避免切换会话/窗口后思考区丢失
        setMessages((prev) => {
          const serverMessages = res.messages || []
          const prevById = new Map(prev.map((m) => [m.id, m]))
          return serverMessages.map((sm) => {
            const local = prevById.get(sm.id)
            if (
              local &&
              sm.role === 'assistant' &&
              local.agent_trace &&
              local.agent_trace.length > 0 &&
              (!sm.agent_trace || sm.agent_trace.length === 0)
            ) {
              return {
                ...sm,
                agent_trace: local.agent_trace,
                thinking_seconds: local.thinking_seconds ?? sm.thinking_seconds,
              }
            }
            return sm
          })
        })
      }
      // 恢复该对话的知识库选择（历史为单 id 则转为数组）
      if (res.knowledge_base_id != null) {
        setSelectedKbIds([res.knowledge_base_id])
      } else {
        setSelectedKbIds([])
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
    setSelectedKbIds([])
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

  const isImageType = (type: string) => (attachmentConfig?.image_types ?? ['image/jpeg', 'image/png', 'image/gif', 'image/webp']).includes(type.toLowerCase())
  const isAllowedFileExt = (name: string) => {
    const ext = name.split('.').pop()?.toLowerCase()
    return ext && (attachmentConfig?.file_extensions ?? ['pdf', 'doc', 'docx', 'txt', 'xlsx', 'xls', 'pptx', 'ppt', 'md']).includes(ext)
  }
  const isVideoExt = (name: string) => {
    const ext = name.split('.').pop()?.toLowerCase()
    return ext && (attachmentConfig?.video_extensions ?? ['mp4', 'webm', 'mov']).includes(ext)
  }

  /** 豆包式：拖拽时显示的附件限制提示文案 */
  const getAttachmentDragHint = () => {
    const cfg = attachmentConfig
    const maxCount = cfg?.max_count ?? 10
    const imageTypes = cfg?.image_types ?? ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    const exts = cfg?.file_extensions ?? ['pdf', 'doc', 'docx', 'txt', 'xlsx', 'xls', 'pptx', 'ppt', 'md']
    const videoExts = cfg?.video_extensions ?? ['mp4', 'webm', 'mov']
    const typeStr = [...imageTypes.map((t: string) => t.replace('image/', '')), ...exts, ...videoExts].join('、')
    return { maxCount, typeStr }
  }

  const addAttachmentFiles = async (files: FileList | File[]) => {
    const cfg = attachmentConfig
    const maxCount = cfg?.max_count ?? 10
    const maxSize = cfg?.max_size_bytes ?? 20 * 1024 * 1024
    const arr = Array.from(files)
    const next: Array<{ id: string; file: File; dataUrl?: string; isImage: boolean; isVideo: boolean; fileName: string; uploadId?: string }> = []
    for (const file of arr) {
      if (attachmentList.length + next.length >= maxCount) {
        message.warning('附件数量已达上限')
        break
      }
      if (file.size > maxSize) {
        message.warning(`跳过 ${file.name}：文件过大`)
        continue
      }
      const isImage = isImageType(file.type)
      const isVideo = !isImage && isVideoExt(file.name)
      const isFile = !isImage && !isVideo && isAllowedFileExt(file.name)
      if (!isImage && !isFile && !isVideo) {
        message.warning(`跳过 ${file.name}：类型不允许`)
        continue
      }
      const item: { id: string; file: File; dataUrl?: string; isImage: boolean; isVideo: boolean; fileName: string; uploadId?: string } = {
        id: `${Date.now()}-${Math.random()}`,
        file,
        fileName: file.name,
        isImage,
        isVideo: !!isVideo,
        dataUrl: undefined,
        uploadId: undefined,
      }
      if (isImage) {
        try {
          item.dataUrl = await fileToDataUrl(file)
        } catch {
          message.warning(`读取失败: ${file.name}`)
          continue
        }
      }
      try {
        const res = await uploadChatFile(file)
        item.uploadId = res.upload_id
      } catch (e) {
        message.warning(`上传失败: ${file.name}，${(e as Error).message}`)
        continue
      }
      next.push(item)
    }
    if (next.length) setAttachmentList(prev => [...prev, ...next])
  }

  const removeAttachment = (id: string) => {
    setAttachmentList(prev => prev.filter(a => a.id !== id))
  }

  const handleSend = async () => {
    if (!inputValue.trim() && attachmentList.length === 0) return

    const displayContent = inputValue.trim() || (attachmentList.length ? '(附件)' : '')
    const attachmentsDisplay: MessageAttachmentDisplay[] = attachmentList.map((a) => {
      const ext = (a.fileName.split('.').pop() || '').toUpperCase()
      const formatMap: Record<string, string> = { PDF: 'PDF', DOC: 'DOC', DOCX: 'DOCX', TXT: 'TXT', XLS: 'XLS', XLSX: 'XLSX', PPT: 'PPT', PPTX: 'PPTX', MD: 'MD', MP4: 'MP4', WEBM: 'WEBM', MOV: 'MOV' }
      return {
        type: (a.isVideo ? 'video' : a.isImage ? 'image' : 'file') as 'image' | 'file' | 'video',
        file_name: a.fileName,
        ...(a.isImage && a.dataUrl ? { dataUrl: a.dataUrl } : {}),
        ...(!a.isImage && ext ? { format: formatMap[ext] || ext } : {}),
      }
    })
    const userMessage: MessageItem = {
      id: Date.now(),
      role: 'user',
      content: displayContent,
      tokens: 0,
      created_at: new Date().toISOString(),
      ...(attachmentsDisplay.length ? { attachments: attachmentsDisplay } : {}),
    }
    setMessages((prev: MessageItem[]) => [...prev, userMessage])
    const messageContent = inputValue.trim() || '(请根据上述附件内容回答)'
    const listToSend = attachmentList
    setInputValue('')
    setAttachmentList([])
    setLoading(true)
    // 新发消息时恢复贴底跟随，便于从最新一条开始看流式输出
    stickToBottomRef.current = true

    const tempAssistantId = Date.now() + 1
    /** done 后与服务端消息 id 对齐，避免 rAF 晚于 done 时用临时 id 匹配失败 */
    let assistantPersistedId: number | null = null
    const isAssistantRow = (id: number) =>
      id === tempAssistantId || (assistantPersistedId != null && id === assistantPersistedId)
    setStreamingAssistantId(tempAssistantId)
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

    const controller = new AbortController()
    abortControllerRef.current = controller

    let traceEvents: Array<{ step?: string; title?: string; text?: string; data?: unknown }> = []
    const traceCharQueue: Array<{ traceIndex: number; chars: string; pos: number }> = []
    let traceDrainScheduled = false
    const TRACE_CHARS_PER_FRAME = 16
    const flushTraceTextPending = () => {
      if (traceCharQueue.length === 0) return
      const byIndex: Record<number, string> = {}
      for (const cur of traceCharQueue) {
        const rest = cur.chars.slice(cur.pos)
        if (rest) byIndex[cur.traceIndex] = (byIndex[cur.traceIndex] ?? '') + rest
      }
      traceCharQueue.length = 0
      traceEvents = traceEvents.map((t, idx) => {
        const extra = byIndex[idx]
        return extra ? { ...t, text: `${t.text ?? ''}${extra}` } : t
      })
      setMessages((prev) =>
        prev.map((m) =>
          isAssistantRow(m.id)
            ? { ...m, agent_trace: traceEvents.length ? traceEvents : undefined }
            : m
        )
      )
    }
    const drainTraceQueue = () => {
      if (!isMountedRef.current) return
      traceDrainScheduled = false
      if (traceCharQueue.length === 0) return
      let left = TRACE_CHARS_PER_FRAME
      const appendByIndex: Record<number, string> = {}
      while (left > 0 && traceCharQueue.length > 0) {
        const cur = traceCharQueue[0]
        const rest = cur.chars.length - cur.pos
        if (rest <= 0) {
          traceCharQueue.shift()
          continue
        }
        const take = Math.min(left, rest)
        const piece = cur.chars.slice(cur.pos, cur.pos + take)
        cur.pos += take
        left -= take
        appendByIndex[cur.traceIndex] = `${appendByIndex[cur.traceIndex] ?? ''}${piece}`
        if (cur.pos >= cur.chars.length) {
          traceCharQueue.shift()
        }
      }
      const touched = Object.keys(appendByIndex)
      if (touched.length > 0) {
        traceEvents = traceEvents.map((t, idx) => {
          const extra = appendByIndex[idx]
          return extra ? { ...t, text: `${t.text ?? ''}${extra}` } : t
        })
        setMessages((prev) =>
          prev.map((m) =>
            isAssistantRow(m.id)
              ? {
                  ...m,
                  agent_trace: traceEvents.length ? traceEvents : undefined,
                }
              : m
          )
        )
      }
      if (traceCharQueue.length > 0) {
        traceDrainScheduled = true
        if (typeof document !== 'undefined' && document.hidden) {
          window.setTimeout(drainTraceQueue, 48)
        } else {
          traceRafRef.current = requestAnimationFrame(drainTraceQueue)
        }
      }
    }

    try {
      const attachmentsToSend = listToSend.map((a) => ({
        type: (a.isVideo ? 'video' : a.isImage ? 'image' : 'file') as 'image' | 'file' | 'video',
        upload_id: a.uploadId,
        file_name: a.fileName,
        ...(a.isImage && a.dataUrl ? { dataUrl: a.dataUrl } : {}),
      }))
      const out = await streamPost(
        'chat/completions/stream',
        {
          content: messageContent,
          knowledge_base_ids: selectedKbIds.length ? selectedKbIds : null,
          conversation_id: currentConvId ?? null,
          super_mode: superMode,
          ...(attachmentsToSend.length ? { attachments: attachmentsToSend } : {}),
        },
        { signal: controller.signal }
      )
      const reader = out.reader
      const decoder = new TextDecoder()
      let buffer = ''
      let newConvId: number | null = null
      let confidence: number | null = null
      let sources: SourceItem[] = []
      let webSources: WebSourceItem[] = []
      let webRetrievedContext: string | null = null
      const tokenQueue: string[] = []
      let drainScheduled = false
      const TOKEN_CHUNKS_PER_FRAME = 3
      const drainTokenQueue = () => {
        if (!isMountedRef.current) return
        drainScheduled = false
        if (tokenQueue.length === 0) return
        let merged = ''
        for (let i = 0; i < TOKEN_CHUNKS_PER_FRAME && tokenQueue.length > 0; i += 1) {
          merged += tokenQueue.shift()!
        }
        setMessages((prev) =>
          prev.map((m) =>
            isAssistantRow(m.id) ? { ...m, content: (m.content || '') + merged } : m
          )
        )
        if (tokenQueue.length > 0) {
          drainScheduled = true
          const schedule =
            typeof document !== 'undefined' && document.hidden
              ? () => window.setTimeout(drainTokenQueue, 48)
              : () => {
                  tokenRafRef.current = requestAnimationFrame(drainTokenQueue)
                }
          schedule()
        }
      }

      while (true) {
          if (!isMountedRef.current) break
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
                content?: string
                conversation_id?: number
                assistant_message_id?: number
                confidence?: number
                sources?: SourceItem[]
                tools_used?: string[]
                web_retrieved_context?: string | null
                web_sources?: WebSourceItem[] | null
                trace?: Array<{ step?: string; title?: string; text?: string; data?: unknown }>
                thinking_seconds?: number
              }
              if (event.type === 'token' && event.content) {
                tokenQueue.push(event.content)
                if (!drainScheduled) {
                  drainScheduled = true
                  if (typeof document !== 'undefined' && document.hidden) {
                    window.setTimeout(drainTokenQueue, 48)
                  } else {
                    tokenRafRef.current = requestAnimationFrame(drainTokenQueue)
                  }
                }
              } else if (event.type === 'trace') {
                const delta = event.trace ?? []
                for (const d of delta) {
                  const rawText = typeof d.text === 'string' && d.text.length > 0 ? d.text : (d.title || '…')
                  // 思考过程统一按逐字动画展示，避免 done 时整段“蹦出”
                  traceEvents = [...(traceEvents ?? []), { ...d, text: '' }]
                  traceCharQueue.push({
                    traceIndex: traceEvents.length - 1,
                    chars: rawText,
                    pos: 0,
                  })
                }
                setMessages((prev) =>
                  prev.map((m) =>
                    isAssistantRow(m.id)
                      ? {
                          ...m,
                          agent_trace: traceEvents.length ? traceEvents : undefined,
                        }
                      : m
                  )
                )
                if (!traceDrainScheduled && traceCharQueue.length > 0) {
                  traceDrainScheduled = true
                  if (typeof document !== 'undefined' && document.hidden) {
                    window.setTimeout(drainTraceQueue, 48)
                  } else {
                    traceRafRef.current = requestAnimationFrame(drainTraceQueue)
                  }
                }
              } else if (event.type === 'done') {
                newConvId = event.conversation_id ?? null
                confidence = event.confidence ?? null
                sources = event.sources ?? []
                webSources = event.web_sources ?? []
                webRetrievedContext = event.web_retrieved_context ?? null
                const toolsUsed = event.tools_used ?? []
                const fromDone = event.trace ?? []
                flushTraceTextPending()
                // 本地已有逐字 trace 时，不用 done 的整段覆盖，避免“瞬间蹦出”
                if (traceEvents.length === 0 && fromDone.length > 0) {
                  traceEvents = fromDone
                }
                const thinkingSec = event.thinking_seconds
                const assistantMessageId = event.assistant_message_id
                if (typeof assistantMessageId === 'number' && assistantMessageId > 0) {
                  assistantPersistedId = assistantMessageId
                  setStreamingAssistantId((sid) => (sid === tempAssistantId ? assistantMessageId : sid))
                  setThinkingOpenMap((prev) => {
                    if (!(tempAssistantId in prev)) return prev
                    const next = { ...prev }
                    next[assistantMessageId] = next[tempAssistantId]!
                    delete next[tempAssistantId]
                    return next
                  })
                }
                setMessages((prev) =>
                  prev.map((m) =>
                    isAssistantRow(m.id)
                      ? {
                          ...m,
                          ...(typeof assistantMessageId === 'number' && assistantMessageId > 0
                            ? { id: assistantMessageId }
                            : {}),
                          confidence: confidence ?? undefined,
                          sources: sources.length ? sources : undefined,
                          tools_used: toolsUsed.length ? toolsUsed : undefined,
                          web_sources: webSources.length ? webSources : undefined,
                          web_retrieved_context: webRetrievedContext ?? undefined,
                          agent_trace: traceEvents.length ? traceEvents : m.agent_trace,
                          thinking_seconds:
                            thinkingSec !== undefined && thinkingSec !== null ? thinkingSec : m.thinking_seconds,
                        }
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
        await loadConversations(true)
        // 超能模式会带 agent_trace（仅流式返回，不落库），这里不强制重拉 messages，避免把 trace 覆盖掉。
        // 如需附件 extracted_text，可手动切换会话触发刷新。
      } catch {
        // ignore
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        message.info('已停止生成')
        loadConversations(true).catch(() => {})
        return
      }
      console.error('发送消息失败:', err)
      const msg = err instanceof Error ? err.message : '发送消息失败'
      message.error(msg)
      setMessages((prev) => prev.filter((m) => !isAssistantRow(m.id)))
    } finally {
      flushTraceTextPending()
      setLoading(false)
      setStreamingAssistantId(null)
      abortControllerRef.current = null
      if (tokenRafRef.current != null) {
        cancelAnimationFrame(tokenRafRef.current)
        tokenRafRef.current = null
      }
      if (traceRafRef.current != null) {
        cancelAnimationFrame(traceRafRef.current)
        traceRafRef.current = null
      }
    }
  }

  const handleStop = () => {
    abortControllerRef.current?.abort()
  }

  const currentConversation = conversations.find((c) => c.id === currentConvId)

  if (pageLoading) return <PageSkeleton rows={5} />

  return (
    <>
    {/* 顶部栏：与主内容区左右边界一致（侧栏宽度 + 48px 留白），侧栏展开/收起都对齐 */}
    <div
      className="chat-top-bar app-animate-in"
      style={{
        position: 'fixed',
        top: 48,
        left: 'calc(var(--app-sider-width, 220px) + 48px)',
        right: 48,
        zIndex: 10,
        paddingTop: 16,
        paddingBottom: 16,
        paddingLeft: 0,
        paddingRight: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        flexWrap: 'wrap',
      }}
    >
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
          style={{ width: 260, minWidth: 260, overflow: 'hidden', paddingLeft: 12, paddingRight: 12 }}
        >
          <span
            style={{
              display: 'inline-block',
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              verticalAlign: 'bottom',
            }}
            title={currentConversation ? (currentConversation.title || '当前对话') : undefined}
          >
            {currentConversation ? currentConversation.title || '当前对话' : '选择对话'}
          </span>
        </Button>
        <Select
          mode="multiple"
          placeholder="选择知识库（可多选，不选则检索全部）"
          allowClear
          value={selectedKbIds}
          onChange={(v: number[]) => setSelectedKbIds(v ?? [])}
          disabled={!!currentConvId}
          options={knowledgeBases.map((kb: KnowledgeBaseListResponse['knowledge_bases'][0]) => ({ value: kb.id, label: `${kb.name}（${kb.chunk_count || 0} 块）` }))}
          style={{ minWidth: 200 }}
        />
        <span style={{ color: 'var(--app-text-muted)', fontSize: 13, whiteSpace: 'nowrap' }}>超能模式</span>
        <Switch checked={superMode} onChange={setSuperMode} />
      </Space>
    </div>

    <div
      className="chat-page app-perspective"
      style={{ minHeight: '100%', display: 'flex', flexDirection: 'column', paddingTop: 100 }}
      onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); dragCounterRef.current++; setDragOver(true) }}
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
      onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); dragCounterRef.current--; if (dragCounterRef.current <= 0) { dragCounterRef.current = 0; setDragOver(false) } }}
      onDrop={(e) => { e.preventDefault(); e.stopPropagation(); dragCounterRef.current = 0; setDragOver(false); if (e.dataTransfer.files.length) addAttachmentFiles(e.dataTransfer.files) }}
    >
      {/* 对话区：仅此区域参与滚动，顶部留白避免被固定栏挡住；与输入框同为拖拽上传区域 */}
      <div
        className="chat-dialogue-area app-animate-in app-animate-in-delay-1"
        style={{ display: 'flex', flexDirection: 'column', flex: 1 }}
      >
        {currentConvId && currentConversation && (
          <div
            style={{
              marginBottom: 20,
              paddingBottom: 16,
              borderBottom: '1px solid var(--app-border-subtle)',
              flexShrink: 0,
              minHeight: 40,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              overflow: 'hidden',
            }}
          >
            <span
              style={{
                fontWeight: 500,
                color: 'var(--app-text-primary)',
                flex: 1,
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={currentConversation.title || '未命名对话'}
            >
              {currentConversation.title || '未命名对话'}
            </span>
            <span style={{ color: 'var(--app-text-muted)', fontSize: 12, flexShrink: 0 }}>
              {new Date(currentConversation.updated_at).toLocaleString('zh-CN')}
            </span>
          </div>
        )}
        <div style={{ marginBottom: 24, padding: `0 24px 220px 24px` }}>
          {messages.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--app-text-muted)', fontSize: 15 }}>
              暂无消息，开始对话吧
            </div>
          ) : (
            <div style={{ maxWidth: CHAT_CONTENT_MAX_WIDTH, margin: '0 auto', paddingLeft: 24, boxSizing: 'border-box' }}>
            <List
              dataSource={messages}
              renderItem={(item: MessageItem) => (
                <List.Item style={{ border: 'none', padding: '20px 0', display: 'block' }}>
                  <div style={{ display: 'flex', justifyContent: item.role === 'user' ? 'flex-end' : 'flex-start' }}>
                    <div
                      className={item.role === 'user' ? 'chat-msg-user' : undefined}
                      style={{
                        maxWidth: '85%',
                        padding: item.role === 'user' ? '12px 16px' : 0,
                        borderRadius: item.role === 'user' ? 12 : 0,
                        background: item.role === 'user' ? 'var(--app-bg-subtle)' : 'transparent',
                        border: item.role === 'user' ? '1px solid var(--app-border-subtle)' : 'none',
                      }}
                    >
                      {/* 用户消息：气泡框；AI 回复：无气泡 */}
                      {/* 用户消息：豆包式先展示附件（图片/文件名），再展示消息内容 */}
                      {item.role === 'user' && item.attachments && item.attachments.length > 0 && (
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 8, justifyContent: 'flex-end' }}>
                            {item.attachments.map((att, idx) =>
                              att.type === 'image' && att.dataUrl ? (
                                <div
                                  key={idx}
                                  role="button"
                                  tabIndex={0}
                                  onClick={() => setImagePreviewUrl(att.dataUrl!)}
                                  onKeyDown={(e) => e.key === 'Enter' && setImagePreviewUrl(att.dataUrl!)}
                                  style={{
                                    cursor: 'pointer',
                                    borderRadius: 8,
                                    overflow: 'hidden',
                                    border: '1px solid var(--app-border)',
                                    flexShrink: 0,
                                  }}
                                >
                                  <img
                                    src={att.dataUrl}
                                    alt={att.file_name}
                                    style={{ width: 72, height: 72, objectFit: 'cover', display: 'block' }}
                                  />
                                </div>
                              ) : att.type === 'image' ? (
                                <div
                                  key={idx}
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 8,
                                    padding: '8px 12px',
                                    backgroundColor: 'var(--app-bg-subtle)',
                                    borderRadius: 8,
                                    border: '1px solid var(--app-border)',
                                  }}
                                >
                                  <PictureOutlined style={{ fontSize: 20, color: 'var(--app-text-secondary)' }} />
                                  <span style={{ fontSize: 12, color: 'var(--app-text-primary)', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={att.file_name}>{att.file_name}</span>
                                </div>
                              ) : att.type === 'video' ? (
                                <div
                                  key={idx}
                                  role="button"
                                  tabIndex={0}
                                  onClick={() => {
                                    setFileDrawerTitle(att.file_name)
                                    setFileDrawerContent(att.extracted_text || '历史会话中的视频仅保留文件名与描述，无法在此播放。')
                                    setFileDrawerVisible(true)
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key !== 'Enter') return
                                    setFileDrawerTitle(att.file_name)
                                    setFileDrawerContent(att.extracted_text || '历史会话中的视频仅保留文件名与描述，无法在此播放。')
                                    setFileDrawerVisible(true)
                                  }}
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 8,
                                    padding: '8px 12px',
                                    backgroundColor: 'var(--app-bg-subtle)',
                                    borderRadius: 8,
                                    border: '1px solid var(--app-border)',
                                    cursor: 'pointer',
                                  }}
                                >
                                  <VideoCameraOutlined style={{ fontSize: 20, color: 'var(--app-text-secondary)' }} />
                                  <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--app-text-primary)', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={att.file_name}>{att.file_name}</span>
                                  {att.format && <span style={{ fontSize: 11, color: 'var(--app-text-muted)' }}>{att.format}</span>}
                                </div>
                              ) : (
                                <div
                                  key={idx}
                                  role="button"
                                  tabIndex={0}
                                  onClick={() => {
                                    setFileDrawerTitle(att.file_name)
                                    setFileDrawerContent(att.extracted_text || '历史会话中的附件仅保留文件名与类型，无法在此查看文件内容。')
                                    setFileDrawerVisible(true)
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key !== 'Enter') return
                                    setFileDrawerTitle(att.file_name)
                                    setFileDrawerContent(att.extracted_text || '历史会话中的附件仅保留文件名与类型，无法在此查看文件内容。')
                                    setFileDrawerVisible(true)
                                  }}
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 8,
                                    padding: '8px 12px',
                                    backgroundColor: 'var(--app-bg-subtle)',
                                    borderRadius: 8,
                                    border: '1px solid var(--app-border)',
                                    cursor: 'pointer',
                                  }}
                                >
                                  <FileTextOutlined style={{ fontSize: 20, color: 'var(--app-text-secondary)' }} />
                                  <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--app-text-primary)', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={att.file_name}>{att.file_name}</span>
                                  {att.format && <span style={{ fontSize: 11, color: 'var(--app-text-muted)' }}>{att.format}</span>}
                                </div>
                              )
                            )}
                          </div>
                        )}
                        {/* 豆包式超能模式：思考过程在正文上方，可折叠；标题展示「思考中/已完成 (时长)」 */}
                        {item.role === 'assistant' && item.agent_trace && item.agent_trace.length > 0 && (
                          <div style={{ marginBottom: item.content?.trim() ? 12 : 0 }}>
                            {(() => {
                              const mergedTrace = mergeAgentTrace(item.agent_trace as AgentTraceItem[])
                              const isAutoOpen = loading && streamingAssistantId === item.id && item.thinking_seconds == null
                              const isOpen = thinkingOpenMap[item.id] ?? isAutoOpen
                              const previewLines = mergedTrace
                                .map((t) => (t.text || t.title || '…').trim())
                                .filter(Boolean)
                                .slice(-3)
                              const stablePreviewLines = [
                                previewLines[0] || '',
                                previewLines[1] || '',
                                previewLines[2] || '',
                              ]
                              return (
                                <>
                                  <Collapse
                                    key={`think-${item.id}-${item.thinking_seconds ?? 'live'}`}
                                    size="small"
                                    activeKey={isOpen ? ['agent_trace'] : []}
                                    onChange={(keys) => {
                                      const arr = Array.isArray(keys) ? keys : [keys]
                                      const opened = arr.includes('agent_trace')
                                      setThinkingOpenMap((prev) => ({ ...prev, [item.id]: opened }))
                                    }}
                                    items={[
                                      {
                                        key: 'agent_trace',
                                        label: (
                                          <span style={{ fontSize: 12, color: 'var(--app-text-muted)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                            {loading && streamingAssistantId === item.id && item.thinking_seconds == null && (
                                              <LoadingOutlined spin />
                                            )}
                                            {typeof item.thinking_seconds === 'number'
                                              ? `已完成 (${formatThinkingDuration(item.thinking_seconds)})`
                                              : loading && streamingAssistantId === item.id
                                                ? `思考中 (${thinkingLiveSec}s)`
                                                : '思考过程'}
                                          </span>
                                        ),
                                        children: (
                                          <div
                                            style={{
                                              fontSize: 13,
                                              color: 'var(--app-text-secondary)',
                                              lineHeight: 1.75,
                                              whiteSpace: 'pre-wrap',
                                              wordBreak: 'break-word',
                                            }}
                                          >
                                            {mergedTrace.map((t, i) => (
                                              <p key={i} style={{ marginBottom: 12, marginTop: 0 }}>
                                                {t.title ? (
                                                  <>
                                                    <span style={{ fontWeight: 600, color: 'var(--app-text-primary)' }}>{t.title}</span>
                                                    {(t.text || '').trim() ? (
                                                      <>
                                                        <br />
                                                        <span style={{ display: 'block' }}>
                                                          {(t.text || '')
                                                            .split('\n')
                                                            .map((line) => line.trim())
                                                            .filter(Boolean)
                                                            .map((line, idx) => (
                                                              <span
                                                                key={idx}
                                                                style={{
                                                                  display: 'flex',
                                                                  alignItems: 'flex-start',
                                                                  gap: 8,
                                                                  marginTop: idx === 0 ? 2 : 4,
                                                                }}
                                                              >
                                                                <span
                                                                  aria-hidden
                                                                  style={{
                                                                    width: 8,
                                                                    height: 8,
                                                                    borderRadius: '50%',
                                                                    background: 'var(--app-text-muted)',
                                                                    marginTop: 7,
                                                                    flex: '0 0 8px',
                                                                    opacity: 0.75,
                                                                  }}
                                                                />
                                                                <span style={{ flex: 1 }}>{line}</span>
                                                              </span>
                                                            ))}
                                                        </span>
                                                      </>
                                                    ) : null}
                                                  </>
                                                ) : (
                                                  t.text || '…'
                                                )}
                                              </p>
                                            ))}
                                            {loading && streamingAssistantId === item.id && item.thinking_seconds == null && (
                                              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: 'var(--app-text-muted)' }}>
                                                <LoadingOutlined spin />
                                                <span>正在检索/调用工具，请稍候...</span>
                                              </div>
                                            )}
                                          </div>
                                        ),
                                      },
                                    ]}
                                  />
                            {!isOpen && (
                                    <div
                                      style={{
                                        marginTop: 8,
                                        padding: '8px 12px',
                                        borderRadius: 8,
                                        background: 'var(--app-bg-subtle)',
                                        border: '1px solid var(--app-border)',
                                        color: 'var(--app-text-secondary)',
                                        fontSize: 12,
                                  lineHeight: '22px',
                                  height: 82, // 固定三行高度，避免流式追加时边框抖动
                                  boxSizing: 'border-box',
                                      }}
                                    >
                                {stablePreviewLines.map((line, idx) => (
                                        <div
                                          key={idx}
                                          style={{
                                            whiteSpace: 'nowrap',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                      minHeight: 22,
                                          }}
                                        >
                                    {line || '\u00A0'}
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </>
                              )
                            })()}
                          </div>
                        )}
                        <div style={{ marginBottom: (item.max_confidence_context || item.retrieved_context) ? 12 : 0 }}>
                          {parseContentWithCharts(item.content).map((part, idx) =>
                            part.type === 'text' ? (
                              <div key={idx} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--app-text-primary)', lineHeight: 1.7, fontSize: 15 }}>
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
                                        role="button"
                                        tabIndex={0}
                                        className="app-card-3d-subtle"
                                        onClick={() => setSourcePreview(s)}
                                        onKeyDown={(e) => e.key === 'Enter' && setSourcePreview(s)}
                                        style={{
                                          marginBottom: 8,
                                          padding: 8,
                                          backgroundColor: 'var(--app-bg-muted)',
                                          borderRadius: 8,
                                          borderLeft: '3px solid var(--app-accent)',
                                          cursor: 'pointer',
                                        }}
                                        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--app-list-hover)' }}
                                        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'var(--app-bg-muted)' }}
                                      >
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8, flexWrap: 'wrap' }}>
                                          <span style={{ fontWeight: 500, color: 'var(--app-text-primary)' }}>
                                            {s.original_filename} · 第 {s.chunk_index + 1} 段
                                            {typeof s.score === 'number' && Number.isFinite(s.score) ? (
                                              <span style={{ fontWeight: 400, color: 'var(--app-text-muted)', marginLeft: 8 }}>
                                                相关性 {(s.score * 100).toFixed(1)}%
                                              </span>
                                            ) : null}
                                          </span>
                                          <span style={{ fontSize: 12, color: 'var(--app-accent)' }}>点击查看原文</span>
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
                        {/* 联网检索内容（与知识库来源区分展示） */}
                        {(item.web_sources && item.web_sources.length > 0) || (item.web_retrieved_context && item.web_retrieved_context.trim()) ? (
                          <Collapse
                            size="small"
                            style={{ marginTop: 12 }}
                            items={[
                              {
                                key: 'web',
                                label: (
                                  <span style={{ fontSize: 12, color: '#52c41a' }}>
                                    <GlobalOutlined /> 联网检索内容
                                    {item.web_sources && item.web_sources.length > 0 ? `（${item.web_sources.length} 条）` : ''}
                                  </span>
                                ),
                                children: (
                                  <div style={{ fontSize: 12, color: 'var(--app-text-muted)' }}>
                                    {item.web_retrieved_context && item.web_retrieved_context.trim() && (
                                      <div style={{
                                        marginBottom: 12,
                                        padding: 8,
                                        backgroundColor: 'var(--app-bg-muted)',
                                        borderRadius: 4,
                                        whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-word',
                                        borderLeft: '3px solid #52c41a',
                                      }}>
                                        {item.web_retrieved_context}
                                      </div>
                                    )}
                                    {item.web_sources && item.web_sources.map((w: WebSourceItem, i: number) => (
                                      <div
                                        key={i}
                                        style={{
                                          marginBottom: 8,
                                          padding: 8,
                                          backgroundColor: 'var(--app-bg-muted)',
                                          borderRadius: 8,
                                          borderLeft: '3px solid #52c41a',
                                        }}
                                      >
                                        <a href={w.url} target="_blank" rel="noopener noreferrer" style={{ fontWeight: 500, color: '#52c41a' }}>
                                          {w.title || w.url || `链接 ${i + 1}`}
                                        </a>
                                        {w.snippet && (
                                          <div style={{ marginTop: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--app-text-primary)' }}>
                                            {w.snippet}
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                ),
                              },
                            ]}
                          />
                        ) : null}
                      {/* AI 消息底部显示置信度（不显示头像和「AI助手」时保留） */}
                      {item.role === 'assistant' && item.confidence !== undefined && item.confidence !== null && (
                        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--app-text-muted)' }}>
                          置信度: {(item.confidence * 100).toFixed(1)}% {item.confidence < 0.6 ? '(低)' : ''}
                        </div>
                      )}
                    </div>
                  </div>
                </List.Item>
              )}
            />
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>
    </div>

    {/* 豆包式：拖拽时背景模糊，中间提示清晰可见（未松开鼠标前） */}
    {dragOver && (
      <div
        className="chat-drag-overlay"
        style={{
          position: 'fixed',
          top: 140,
          left: 'calc(var(--app-sider-width, 220px) + 48px)',
          right: 48,
          bottom: 0,
          zIndex: 15,
          background: 'rgba(255,255,255,0.15)',
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          border: '2px dashed var(--app-border-info)',
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          pointerEvents: 'none',
        }}
      >
        <span style={{ fontSize: 16, color: 'var(--app-text-primary)', fontWeight: 600 }}>在此处拖放文件</span>
        <span style={{ fontSize: 13, color: 'var(--app-text-muted)' }}>
          文件数量：最多 {getAttachmentDragHint().maxCount} 个
        </span>
        <span style={{ fontSize: 13, color: 'var(--app-text-muted)' }}>
          文件类型：{getAttachmentDragHint().typeStr}
        </span>
      </div>
    )}

    {/* 输入框：与主内容区左右边界一致（AI 消息左边框、用户消息/发送按钮右边框对齐），侧栏展开/收起都保持 */}
    <div
      className="chat-input-bar tech-input-wrap"
      style={{
        position: 'fixed',
        bottom: 24,
        left: 'calc(var(--app-sider-width, 220px) + 48px)',
        right: 48,
        paddingTop: 24,
        paddingBottom: 24,
        paddingLeft: 0,
        paddingRight: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
      }}
    >
      <div style={{ width: '100%', maxWidth: CHAT_CONTENT_MAX_WIDTH, margin: '0 auto', boxSizing: 'border-box' }}>
        <div
          onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); dragCounterRef.current++; setDragOver(true) }}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
          onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); dragCounterRef.current--; if (dragCounterRef.current <= 0) { dragCounterRef.current = 0; setDragOver(false) } }}
          onDrop={(e) => { e.preventDefault(); e.stopPropagation(); dragCounterRef.current = 0; setDragOver(false); if (e.dataTransfer.files.length) addAttachmentFiles(e.dataTransfer.files) }}
          className="chat-input-inner"
          style={{
            ...(dragOver && { background: 'var(--app-bg-subtle)' }),
            transition: 'background .15s',
            overflow: 'hidden',
          }}
        >
          {/* 豆包式：上方仅输入框，回车发送 */}
          <Input
            value={inputValue}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputValue(e.target.value)}
            onPressEnter={(e) => { e.preventDefault(); handleSend() }}
            placeholder="发消息或输入 / 选择技能"
            variant="borderless"
            size="large"
            style={{ fontSize: 16, minHeight: 112, padding: '32px 20px', border: 'none', borderBottom: 'none', background: 'transparent' }}
            disabled={loading}
          />
          {/* 下部：附件列表 + 附件上传按钮，无发送按钮（回车发送） */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px' }}>
            <Button
              type="text"
              size="small"
              icon={<PaperClipOutlined />}
              onClick={() => fileInputRef.current?.click()}
              disabled={loading}
              style={{ color: 'var(--app-text-secondary)' }}
              title="上传附件"
            />
            <input
              ref={fileInputRef}
              type="file"
              accept={[
                ...(attachmentConfig?.image_types ?? ['image/jpeg', 'image/png', 'image/gif', 'image/webp']),
                ...(attachmentConfig?.file_extensions ?? ['pdf', 'doc', 'docx', 'txt', 'xlsx', 'xls', 'pptx', 'ppt', 'md']).map(e => `.${e}`),
                ...(attachmentConfig?.video_extensions ?? ['mp4', 'webm', 'mov']).map(e => `.${e}`),
              ].join(',')}
              multiple
              style={{ display: 'none' }}
              onChange={(e) => { const f = e.target.files; if (f?.length) addAttachmentFiles(f); e.target.value = '' }}
            />
            {attachmentList.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                {attachmentList.map((a) => (
                  <div key={a.id} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', borderRadius: 6, border: '1px solid var(--app-border-subtle)', overflow: 'hidden', background: 'var(--app-bg-subtle)' }}>
                    {a.isImage && a.dataUrl ? (
                      <img src={a.dataUrl} alt="" style={{ width: 32, height: 32, objectFit: 'cover' }} />
                    ) : a.isVideo ? (
                      <VideoCameraOutlined style={{ fontSize: 18, color: 'var(--app-text-secondary)', marginLeft: 6 }} />
                    ) : null}
                    {(!a.isImage || !a.dataUrl) && (
                      <span style={{ padding: '4px 8px', fontSize: 12, color: 'var(--app-text-secondary)', maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.fileName}</span>
                    )}
                    <Button type="text" size="small" icon={<CloseOutlined />} style={{ minWidth: 22, height: 22, padding: 0, color: 'var(--app-text-muted)' }} onClick={() => removeAttachment(a.id)} />
                  </div>
                ))}
              </div>
            )}
            {loading && (
              <Button type="link" danger size="small" icon={<StopOutlined />} onClick={handleStop} style={{ marginLeft: 'auto' }}>
                停止
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>

      <Modal
        title={
          sourcePreview
            ? `${sourcePreview.original_filename} · 第 ${(sourcePreview.chunk_index ?? 0) + 1} 段${
                typeof sourcePreview.score === 'number' && Number.isFinite(sourcePreview.score)
                  ? ` · 相关性 ${(sourcePreview.score * 100).toFixed(1)}%`
                  : ''
              }`
            : '引用来源'
        }
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
                minHeight: 56,
                overflow: 'hidden',
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
                title={
                  <div
                    style={{
                      fontWeight: currentConvId === conv.id ? 600 : 400,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={conv.title || '未命名对话'}
                  >
                    {conv.title || '未命名对话'}
                  </div>
                }
                description={
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--app-text-muted)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {new Date(conv.updated_at).toLocaleString('zh-CN')}
                  </div>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
      {/* 豆包式：图片点击放大 */}
      <Modal
        open={!!imagePreviewUrl}
        footer={null}
        closable
        onCancel={() => setImagePreviewUrl(null)}
        width="80%"
        style={{ maxWidth: 800 }}
        styles={{ body: { padding: 0, textAlign: 'center' } }}
      >
        {imagePreviewUrl && (
          <img src={imagePreviewUrl} alt="" style={{ maxWidth: '100%', maxHeight: '80vh', objectFit: 'contain' }} />
        )}
      </Modal>
      {/* 豆包式：文件点击侧边栏查看（可滚动） */}
      <Drawer
        title={fileDrawerTitle}
        placement="right"
        width={400}
        open={fileDrawerVisible}
        onClose={() => setFileDrawerVisible(false)}
      >
        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--app-text-secondary)', fontSize: 13, overflowY: 'auto', maxHeight: '100%' }}>
          {fileDrawerContent}
        </div>
      </Drawer>
    </>
  )
}
