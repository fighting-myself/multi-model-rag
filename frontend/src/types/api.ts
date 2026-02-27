/** API 响应类型，供各页面使用，避免 any 和飘红 */

export interface DashboardStats {
  file_count: number
  knowledge_base_count: number
  conversation_count: number
}

export interface FileItem {
  id: number
  original_filename?: string
  filename?: string
  file_type: string
  file_size: number
  status: string
  created_at: string
}

export interface FileListResponse {
  files: FileItem[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeBaseItem {
  id: number
  name: string
  description?: string
  file_count: number
  chunk_count: number
  created_at: string
  chunk_size?: number
  chunk_overlap?: number
  chunk_max_expand_ratio?: string
  embedding_model?: string
  llm_model?: string
  temperature?: number
  enable_rerank?: boolean
  enable_hybrid?: boolean
}

/** 提交异步任务后的响应 */
export interface TaskEnqueueResponse {
  task_id?: string | null
  message?: string
  sync?: boolean
  result?: Record<string, unknown>
}

/** 任务状态（轮询用） */
export interface TaskStatusResponse {
  task_id: string
  status: string // PENDING | STARTED | SUCCESS | FAILURE | RETRY
  result?: { kb_id?: number; file_count?: number; chunk_count?: number; skipped?: { file_id: number; original_filename: string; reason: string }[]; reindexed_files?: number; total_files?: number }
  error?: string
  traceback?: string
}

/** 用量与限流快照（仪表盘/计费展示） */
export interface UsageLimitsResponse {
  upload_today: number
  upload_limit_per_day: number
  conversation_today: number
  conversation_limit_per_day: number
  search_current_second: number
  search_qps_limit: number
}

export interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBaseItem[]
  total: number
  page: number
  page_size: number
}

/** 知识库内单条文件（含该文件在本库中的分块数） */
export interface KnowledgeBaseFileItem {
  file_id: number
  original_filename: string
  file_type: string
  file_size: number
  chunk_count_in_kb: number
  added_at?: string
}

export interface KnowledgeBaseFileListResponse {
  files: KnowledgeBaseFileItem[]
  total: number
  page: number
  page_size: number
}

/** 添加文件到知识库时被跳过的项 */
export interface SkippedFileItem {
  file_id: number
  original_filename: string
  reason: string
}

/** 添加文件到知识库的响应 */
export interface AddFilesToKnowledgeBaseResponse extends KnowledgeBaseItem {
  skipped: SkippedFileItem[]
}

/** 单条分块（查看分块内容） */
export interface ChunkItem {
  id: number
  chunk_index: number
  content: string
}

export interface ChunkListResponse {
  chunks: ChunkItem[]
}

export interface UsageResponse {
  file_uploads: number
  storage_mb: number
  queries: number
  tokens: number
  cost: number
  period_start: string
  period_end: string
}

export interface PlanItem {
  id: number
  name: string
  description?: string
  price: number
  monthly_credits?: number
  features?: Record<string, unknown>
}

export interface PlanListResponse {
  plans: PlanItem[]
  total: number
}

/** 引用来源（溯源） */
export interface SourceItem {
  file_id: number
  original_filename: string
  chunk_index: number
  snippet: string
  knowledge_base_id?: number | null
}

export interface MessageItem {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  tokens: number
  model?: string
  created_at: string
  confidence?: number  // 检索置信度（0-1）
  retrieved_context?: string  // 检索到的上下文内容
  max_confidence_context?: string  // 最高置信度对应的单个上下文
  sources?: SourceItem[]  // 引用来源列表
  tools_used?: string[]  // 本回复调用的 MCP 工具名列表
}

export interface ConversationItem {
  id: number
  title?: string
  knowledge_base_id?: number
  created_at: string
  updated_at: string
  messages?: MessageItem[]
}

export interface ConversationListResponse {
  conversations: ConversationItem[]
  total: number
  page: number
  page_size: number
}

export interface ChatCompletionResponse {
  conversation_id: number
  message: string
  tokens: number
  model: string
  created_at: string
  confidence?: number  // 检索置信度（0-1）
  retrieved_context?: string  // 检索到的上下文内容
  max_confidence_context?: string  // 最高置信度对应的单个上下文
  sources?: SourceItem[]  // 引用来源列表
  tools_used?: string[]  // 本回复调用的 MCP 工具名列表
}

/** 以文搜图 / 图搜图单条结果 */
export interface ImageSearchItem {
  file_id: number
  original_filename: string
  file_type: string
  snippet?: string
  score?: number
}

export interface ImageSearchResponse {
  files: ImageSearchItem[]
}

/** 统一检索单条结果（文档+图片混合） */
export interface UnifiedSearchItem {
  chunk_id: number
  file_id: number
  original_filename: string
  file_type: string
  snippet: string
  score: number
  is_image: boolean
}

export interface UnifiedSearchResponse {
  items: UnifiedSearchItem[]
}

/** 审计日志单条 */
export interface AuditLogItem {
  id: number
  user_id: number
  action: string
  resource_type: string | null
  resource_id: string | null
  detail: string | null
  ip: string | null
  created_at: string
}

export interface AuditLogListResponse {
  items: AuditLogItem[]
  total: number
  page: number
  page_size: number
}

/** MCP 服务 */
export interface McpServerItem {
  id: number
  name: string
  transport_type: 'stdio' | 'streamable_http' | 'sse'
  config: Record<string, unknown>
  enabled: boolean
}

/** MCP 工具项 */
export interface McpToolItem {
  name: string
  description: string
  inputSchema: Record<string, unknown>
}

export interface McpToolsListResponse {
  server_id: number
  server_name: string
  tools: McpToolItem[]
}
