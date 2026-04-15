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
  /** 该片段相关性分数 0–1 */
  score?: number | null
}

/** 联网检索来源（标题、链接、摘要） */
export interface WebSourceItem {
  title: string
  url: string
  snippet: string
}

/** 用户消息中附件的展示信息（豆包式：图片展示缩略图，文件/视频展示文件名+格式） */
export interface MessageAttachmentDisplay {
  type: 'image' | 'file' | 'video'
  file_name: string
  dataUrl?: string       // 仅图片：用于在气泡内展示，持久化后切换会话仍能显示
  format?: string        // 文件/视频：如 PDF、MP4
  extracted_text?: string // 文件/视频：解析后的文本或视频描述，供侧栏可滚动查看
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
  web_retrieved_context?: string  // 联网检索得到的文本
  web_sources?: WebSourceItem[]  // 联网检索来源列表
  /** 超能模式中间过程轨迹（流式实时 + 会话接口从库中恢复） */
  agent_trace?: Array<{ step?: string; title?: string; text?: string; data?: unknown }>
  /** 超能模式思考阶段耗时（秒；流式为管线阶段，与库中一致） */
  thinking_seconds?: number
  /** 用户消息附件的展示用（图片缩略图、文件名+格式） */
  attachments?: MessageAttachmentDisplay[]
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
  web_retrieved_context?: string  // 联网检索得到的文本
  web_sources?: WebSourceItem[]  // 联网检索来源列表
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
  knowledge_base_id?: number | null
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

/** 外接平台连接 */
export interface ExternalConnectionItem {
  id: number
  name: string
  account?: string | null
  /** 后端脱敏：如有密码则为 '***'，否则为 null */
  password?: string | null
  cookies_present: boolean
  enabled: boolean
}

/** 召回率评测：单条 benchmark 样本 */
export interface BenchmarkItem {
  query: string
  relevant_chunk_ids: number[]
}

/** 检索方式配置 */
export interface RetrievalConfig {
  retrieval_mode: 'vector' | 'fulltext' | 'hybrid'
  use_rerank: boolean
  use_query_expand: boolean
}

/** 发起召回率评测请求 */
export interface RecallRunRequest {
  knowledge_base_id: number
  retrieval_config: RetrievalConfig
  benchmark: { items: BenchmarkItem[] }
  top_k_list?: number[]
}

/** 召回率评测结果 */
export interface RecallRunResponse {
  config_snapshot: Record<string, unknown>
  metrics: {
    recall_at_1?: number
    recall_at_5?: number
    recall_at_10?: number
    recall_at_20?: number
    hit_at_1?: number
    hit_at_5?: number
    hit_at_10?: number
    hit_at_20?: number
    mrr: number
    num_queries: number
    num_items_with_relevant: number
  }
  details: Array<{
    query: string
    retrieved_ids: number[]
    relevant_ids: number[]
    recall_at_k: Record<number, number>
    hit_at_k: Record<number, number>
    mrr: number
  }>
}

/** 评测数据集（保存/加载） */
export interface BenchmarkDatasetItem {
  id: number
  user_id: number
  knowledge_base_id: number | null
  name: string
  description: string | null
  items: BenchmarkItem[]
  created_at?: string
  updated_at?: string
}

export interface BenchmarkDatasetListResponse {
  datasets: BenchmarkDatasetItem[]
  total: number
  page: number
  page_size: number
}

/** Advanced RAG 单条指标说明（六大指标） */
export interface RAGMetricItem {
  priority: number
  id: string
  name: string
  name_en: string
  description: string
  tip: string
  link: string | null
  unit: string | null
}

/** Advanced RAG 六大指标接口 */
export interface RAGMetricsResponse {
  metrics: RAGMetricItem[]
  latency_standards: Record<string, string>
}

export interface RAGMetricsPrecheckResponse {
  knowledge_base_id?: number | null
  knowledge_base_name?: string | null
  eval_mode: 'normal' | 'super'
  metric_id?: string | null
  chunk_count: number
  avg_chunk_chars: number
  sample_source: 'default_seed' | 'adaptive_kb'
  memory_context_disabled_for_eval: boolean
  warnings: string[]
}

export interface AgentToolItem {
  id: number
  name: string
  code: string
  description?: string
  tool_type: string
  parameters_schema?: Record<string, unknown> | null
  config?: Record<string, unknown> | null
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface SingleAgentRunResponse {
  paradigm: 'react' | 'plan_execute' | 'reflexion' | 'rewoo'
  answer: string
  tools_used: string[]
  trace: Array<{ step?: string; title?: string; text?: string; data?: unknown }>
}

export interface SingleAgentRunRequest {
  query: string
  paradigm: 'react' | 'plan_execute' | 'reflexion' | 'rewoo'
}

export interface MultiAgentRunRequest {
  query: string
  scene: 'finance_research' | 'market_ops' | 'compliance_risk' | 'product_strategy'
  finance_params?: {
    symbol?: string
    time_window?: string
    risk_preference?: string
  }
}

/** 单条执行轨迹（含思考过程与输出，供流式与最终结果共用） */
export interface MultiAgentTraceItem {
  step?: string
  title?: string
  text?: string
  phase?: string
  thinking?: string
  output?: string
  /** 与 Crew 控制台一致的块（# Agent / ## Task / ## Final Answer） */
  crew_log_style?: string
  /** TaskOutput.messages 序列化，含传给下一步的 context */
  messages_json?: string
  expected_output?: string
  data?: unknown
}

export interface MultiAgentRunResponse {
  answer: string
  scene: 'finance_research' | 'market_ops' | 'compliance_risk' | 'product_strategy'
  framework: string
  traces: MultiAgentTraceItem[]
}

/** SSE：`POST /multi-agent/run/stream` 解析后的载荷 */
export type MultiAgentSsePayload =
  | { type: 'trace'; item: MultiAgentTraceItem }
  | { type: 'done'; answer: string; scene: string; framework: string }
  | { type: 'error'; detail: string }
