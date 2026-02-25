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
}
