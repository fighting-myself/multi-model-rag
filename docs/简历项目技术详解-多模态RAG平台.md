# 简历项目技术详解：多模态 RAG 智能问答平台（multi-model-rag）

> 本文面向**简历/面试**：说明本项目**实际实现细节**，并补充**业界常见做法**与**复杂场景下的上下文策略**，便于提炼项目亮点与答辩话术。  
> 代码路径以仓库 `backend/`、`frontend/` 为准；配置见 `backend/app/core/config.py`。

---

## 一、项目定位（可写进简历的一句话）

企业级 **多模态 RAG 问答与评测平台**：支持多格式文档解析与向量化入库、**向量 + 全文（BM25）混合检索 + RRF 融合 + 交叉编码器 Rerank**、流式对话与片段溯源；可选 **Advanced RAG（LlamaIndex 查询变换）**、**联网检索**、**超能模式（意图路由 + MCP + Skills + web_fetch）**；配套 **RAG 召回/六大指标评测**、**多模态以文搜图/以图搜图**、用户鉴权、限流、审计、异步任务与缓存。

---

## 二、技术栈总览（与实现对齐）

| 层级 | 技术 |
|------|------|
| 后端 API | FastAPI、Pydantic、异步 SQLAlchemy 2.0 |
| 关系库 | PostgreSQL / MySQL（`asyncmy`） |
| 缓存 | Redis（会话列表、上传临时解析结果、统计等） |
| 异步任务 | Celery（知识库加文件、重索引等，`kb_tasks.py`） |
| 向量库 | **Zilliz Cloud / Milvus 兼容**（`VECTOR_DB_TYPE=zilliz`）或 **Qdrant** |
| 对象存储 | MinIO（文件本体存储，与业务表关联） |
| 前端 | React 18、TypeScript、Vite、Ant Design 5、Axios、Zustand、ECharts |
| LLM 接入 | OpenAI 兼容协议（`OPENAI_BASE_URL`），生产可接 **阿里云百炼 DashScope**（`USE_DASHSCOPE`、`DASHSCOPE_*`） |

---

## 三、模型与调用方式（细节）

### 3.1 主对话模型（LLM）

- **配置项**：`LLM_MODEL`（默认如 `qwen3-vl-plus`），走兼容 OpenAI 的 `chat.completions`。
- **实现**：`llm_service.py` 原生异步客户端；若 `USE_LANGCHAIN=True` 则 **`langchain_llm.py`** 用 LangChain `ChatOpenAI` 封装同样端点，用于流式、工具调用链等。
- **流式**：`chat_completion_stream`；百炼侧可能触发 **内容安全**（`DataInspectionFailed`），`langchain_llm` 中对长上下文有 **截断重试** 与 **友好降级文案**，避免整条链路只报错无输出。

### 3.2 向量模型（Embedding）

- **配置**：`EMBEDDING_MODEL`（如 `qwen3-vl-embedding`），**维度**与 Milvus 集合字段一致（`ZILLIZ_DIM`，常见 1536）。
- **实现**：`embedding_service.py` 调用 **DashScope 多模态 Embedding HTTP API**（非仅 `v1/embeddings`），`input.contents` 支持 `[{"text": "..."}]`；**单条文本截断至 8192 字符**；批量 **每批最多 20 条**。
- **多模态**：`get_embedding_for_image` 对图片 base64 发 `{"image": "data:image/..."}`，与文本 **同一向量空间**，支撑图搜图、以文搜图。

### 3.3 重排序（Rerank）

- **配置**：`RERANK_MODEL`（如 `qwen3-rerank`）。
- **实现**：`rerank_service.py` 调用百炼 **text-rerank** HTTP API；返回 `index` + `relevance_score`；失败时 **回退为原顺序 + 默认分数 0.5**，避免检索链路中断。

### 3.4 OCR / 视觉 / 视频

- **图片（聊天附件、扫描 PDF 等）**：`ocr_service.py` 用 **OpenAI 兼容多模态消息**（`AsyncOpenAI` + `data_url`），提示词区分「有字则 OCR / 无字则描述」，服务检索与问答。
- **扫描版 PDF**：`knowledge_base_service._extract_pdf_ocr` 使用 **pdf2image** 按页渲染 → 每页再走 `extract_text_from_image`；触发条件与 `PDF_OCR_MIN_CHARS`、`PDF_OCR_DPI` 相关。
- **视频附件**：`video_extract_service.py` 将整段视频 **base64 为 data URL**，用 **支持视频的 VL 模型**（如 `qwen3-vl-plus`）生成**单段长描述**，注入问答上下文（非逐帧 OpenCV 方案）。

---

## 四、各类型文件如何解析（入库与聊天附件通用逻辑）

核心静态方法：**`KnowledgeBaseService._extract_text(content: bytes, file_type: str)`**（`knowledge_base_service.py`）。聊天上传在 `api/v1/chat.py` 中亦调用同一套解析（非图片路径）。

### 4.1 纯文本与 Markup

| 类型 | 做法 |
|------|------|
| **TXT** | `utf-8` 解码，`errors="ignore"`。 |
| **Markdown** | 同 TXT，原样保留。 |
| **HTML** | BeautifulSoup 去 `script/style`，`get_text` 换行拼接。 |

### 4.2 PDF（重点）

1. **正文**：优先 **PyPDF2** `PdfReader` 逐页 `extract_text()` 拼接。  
2. **兜底**：若几乎无字，换 **pdfplumber** 逐页 `extract_text()`。  
3. **表格**：**pdfplumber** `page.extract_tables()`，格式化为「表：第 N 页表格 M」+ 制表符分隔行，再拼到正文后，**利于表格问答**。  
4. **扫描件/字太少**：可走 **OCR 流水线**（见 3.4）。

**业界常见做法**：生产环境常见 **PyMuPDF（fitz）**、**Unstructured**、**LayoutParser** 做版式分析；本项目以 **轻依赖 + 双引擎文本 + 专用表格抽取** 为主，平衡部署成本与效果。

### 4.3 Word / PPT

| 类型 | 做法 |
|------|------|
| **DOCX** | `python-docx`：段落文本 + **表格单元格**遍历拼接。 |
| **PPTX** | `python-pptx`：每页 shape 文本；**含表格**则再遍历 `shape.table` 单元格。 |

### 4.4 Excel（XLSX）

- **openpyxl**，`read_only=True`, `data_only=True`（公式取计算值）。  
- 按 **sheet** 输出：`表：<sheet名>`，每行 `iter_rows(values_only=True)`，单元格 `\t` 拼接；多 sheet 用空行分隔合并。  
- **说明**：未做「表头-关系型数据库」建模，而是 **展平为可检索文本**，适合 RAG；业界 BI 场景常配合 **pandas + 结构化 schema** 或 **SQL on CSV**，与本项目目标不同。

### 4.5 ZIP

- 解压后按扩展名白名单（txt/pdf/md/html/docx/pptx/xlsx 等）逐个 `_extract_text`，带 `[文件名]` 前缀合并，适合 **打包资料一次性入库**。

### 4.6 图片与视频（聊天侧）

- **图片**：不走 `_extract_text`，而走 **多模态 LLM** 得一段统一可检索文本（见 3.4）。  
- **视频**：VL 模型整段理解（见 3.4）。  

### 4.7 安全与质量

- **魔数校验**：`file_security_service.py` 扩展名与文件头一致（防伪造）。  
- **敏感信息**：`SENSITIVE_MASK_ENABLED` 时 `sensitive_mask_service` 对身份证、手机等 **脱敏后再入库/检索**（具体规则以代码为准）。  
- **可选**：ClamAV 病毒扫描、禁止可执行扩展名上传。

---

## 五、分块（Chunking）与入库索引

### 5.1 分块策略（`_chunk_text`）

- **参数**：`chunk_size`（默认 500 字）、`overlap`（50）、`max_expand_ratio`（1.3，允许为保整句略超 `chunk_size`）。知识库可 ** per-KB 覆盖**。
- **流程**：  
  - 先处理 **重复句/重复图片描述** 合并，避免向量浪费。  
  - **以句切分**（中英文句号、问号、叹号、换行等正则）。  
  - 句子合并进块时控制长度；超长句再按 **逗号/分号** 子切分。  
  - 块间 **重叠**通过保留末尾若干句实现。  
- **图片类单段描述**：前缀识别「图片内容描述」时 **整段单 chunk**，上限约 2000 字。

**业界对比**：简单固定字符滑窗 vs **语义分块（sentence-transformers 聚类）** vs **结构化分块（Markdown/HTML 标题）**；本项目为 **规则 + 句子边界**，易控延迟与确定性。

### 5.2 向量 ID 与删除一致性

- `vector_store.chunk_id_to_vector_id`：对 `chunk_id` 做 **确定性哈希映射** 为整型 id，插入/删除 Milvus 与 PG **可对应**；删知识库时批量删向量（见 `delete_knowledge_base`）。

### 5.3 异步任务

- 大文件 **加库、重索引** 走 Celery（`kb_tasks.py`），避免 HTTP 超时；任务内 **单独创建 async engine**，避免与 FastAPI 事件循环冲突。

---

## 六、RAG 检索链路（本项目如何实现）

### 6.1 单库 / 多库 / 未选库

- **指定 knowledge_base_id**：单库向量检索 + 全文。  
- **knowledge_base_ids**：多库过滤。  
- **未选库且配置允许**：可在用户 **全部知识库** 上做 **全库候选池**（`RAG_ALL_KB_POOL_K`）、**渐进扩充**（`RAG_ITERATIVE_CHUNK_STEPS`、`RAG_ITERATIVE_MAX_ROUNDS`），并结合 **模型评估是否足够**（见 `chat_service` 内迭代 RAG）。

### 6.2 混合检索与融合

- **向量**：embedding 查询，TopK 命中 chunk。  
- **全文**：关键词/BM25（`RAG_USE_BM25`、`bm25_service`）或简单词频，**多关键词 OR** 从 DB 拉候选。  
- **RRF**：多路排序列表用 **Reciprocal Rank Fusion**（`RRF_K=60`）合并为统一候选池。  
- **Rerank**：对候选 **正文** 与 **query** 调用 DashScope rerank，得到 `relevance_score`，再取 TopN。

### 6.3 置信度与低质检索

- `RAG_CONFIDENCE_THRESHOLD`：综合置信度 **低于阈值** 时可 **不注入片段正文**，仅系统说明，减少幻觉（超能/普通模式分支略有差异，以 `chat_service` 为准）。

### 6.4 可选增强

- **查询扩展**：`RAG_QUERY_EXPAND` 时 `query_expand` 多生成子查询（多一次 LLM，增延迟）。  
- **上下文窗口扩展**：`RAG_CONTEXT_WINDOW_EXPAND` 对命中 chunk **向前后邻块扩展**。  
- **Advanced RAG**：`advanced_rag_service.py` + **LlamaIndex** `OpenAILike` 做 **查询变换**（多子查询），再 **复用同一套** `_rag_context_*` + `optional_queries` 注入，避免重复实现检索。

### 6.5 业界常见范式（答辩可用）

| 范式 | 说明 |
|------|------|
| Naive RAG | 切块 → embed → 相似度 TopK → 拼 prompt |
| Advanced RAG | 查询改写 / HyDE / 子问题 / Agent 选工具 |
| Graph RAG | 实体关系图 + 多跳推理（本项目未作为主路径） |
| Self-RAG / CRAG | 生成中自检检索质量（可通过外层 LLM 评估近似） |

本项目落地：**混合检索 + RRF + Rerank + 置信度门控 +（可选）LlamaIndex 查询变换 +（可选）全库渐进**，属于工程上 **性价比高** 的组合。

---

## 七、对话与上下文管理

### 7.1 会话内历史

- **配置**：`CHAT_CONTEXT_MESSAGE_COUNT`（如最近 8 条完整保留）。  
- **更早消息**：`_summarize_old_messages` 调 **LLM 压缩**为短摘要，前缀 `[对话历史总结]`，再拼最近若干条原文。  
- **超能/流式**：可按场景 `skip_summary=True` **降低首字延迟**（仅截断不总结）。

### 7.2 用户消息构造

- **多模态**：用户 content 可为 **文本 + 图片 URL/base64**（OpenAI 风格数组）。  
- **附件**：先 `/attachments/upload` 解析，Redis 缓存 `extracted_text`；发消息时带 `upload_id` 注入 **「用户上传的文件内容」** 块（与入库解析 **同一套** `_extract_text` 逻辑）。

### 7.3 流式输出

- SSE/JSON 流式返回 token；存库 **完整 assistant 消息**，并可选 **溯源**（chunk 列表、web_sources、agent_trace 等）。

---

## 八、超能模式（Super Mode）与工具

### 8.1 管线概览

1. **意图路由 LLM**：输出 JSON，`need_rag / need_mcp / need_skills` 及 MCP 工具偏好等。  
2. **多轮上下文循环**（最多 `RAG_ITERATIVE_MAX_ROUNDS`）：按需执行 RAG → MCP → Skills，每轮 **评估上下文是否足够**（`_assess_context_and_next_actions`）。  
3. **综合生成**：拼接 MCP/Skills/RAG/系统说明/对话历史，流式调用主 LLM。

### 8.2 Skills 与联网

- **Skills**：`skill_loader` 扫描 `skills/*/SKILL.md`（含 YAML frontmatter），工具侧 **OpenAI function** 来自 `steward_tools` + `web_tools`（`web_fetch`/`web_search`）等。  
- **web_fetch**：`web_tools.py` httpx 拉取 HTML → BeautifulSoup 去噪取正文，**SSRF 防护**（禁内网、私网段）。  
- **二次过滤**：Skills 结果曾用 LLM 判相关性；**web_fetch/web_search** 已做 **免二次误杀**（避免百家号等正文被清空）。  
- **MCP**：`mcp_client_service` 连接已配置 MCP Server，工具列表动态拉取，可与 Skills **分阶段**执行。

### 8.3 联网搜索（非 Skills 独占）

- `web_search_service.py`：面向「即时信息」类 query，HTTP 请求搜索引擎或聚合接口（具体以代码为准），结果 **标题/链接/摘要** 注入上下文；可与 RAG **并行**增强（配置 `ENABLE_WEB_SEARCH`）。

---

## 九、评测与可观测性

- **Recall Evaluation**：benchmark 数据集 + Recall@k / MRR 等（`recall_evaluation_service`）。  
- **Advanced RAG Metrics 页面**：准确率、召回、精准、延迟、幻觉率、QPS 等批量/压测维度（见 README）。  
- **审计 / 计费 / 限流**：`AUDIT_LOG_ENABLED`、`RATE_LIMIT_*` 等。

---

## 十、「市面一般怎么做」与「复杂场景上下文怎么做」（浓缩版）

### 10.1 上下文从哪来

| 来源 | 常见做法 | 本项目 |
|------|----------|--------|
| 知识库 | 检索 TopK + rerank | 混合检索 + RRF + rerank + 阈值 |
| 长文档 | 分段、摘要、层次索引 | 句子分块 + 可选邻块扩展 |
| 多轮对话 | 滑动窗口、摘要压缩 | 最近 N 条 + 远端摘要 |
| 实时信息 | 搜索 API、爬虫 | web_search + web_fetch |
| 工具结果 | Toolformer / ReAct 范式 | 超能模式分阶段 + 评估循环 |
| 合规与安全 | 输出过滤、地域模型 | 百炼内容安全 + 截断重试与提示 |

### 10.2 复杂问题怎么拆

1. **先路由**：是否需要库内知识 / MCP / 联网 / 技能脚本。  
2. **再检索**：单库或多库或全库渐进，避免一次塞满无关 chunk。  
3. **再融合**：RRF 多路合一，Rerank 精排，阈值过滤低质命中。  
4. **再生成**：系统说明约束幻觉；对话历史保持连贯。  
5. **失败兜底**：检索失败提示、工具错误信息回传、内容安全降级文案。

---

## 十一、简历可摘的「量化/亮点」表述示例（按需改写）

- 实现 **PDF 双引擎文本提取 + pdfplumber 表格结构化拼接**，提升表格类问答可用性。  
- 实现 **向量 + BM25 混合检索 + RRF（k=60）+ DashScope 交叉编码器 Rerank** 的完整链路。  
- 基于 **LlamaIndex 查询变换** 与现有检索融合，形成可开关的 **Advanced RAG**，控制首字延迟与效果权衡。  
- **多模态 Embedding** 与 **文本同空间**，支撑 **以文搜图 / 以图搜图** 与统一检索架构。  
- **超能模式**：意图路由 + **多轮工具编排**（MCP/Skills/web_fetch）+ **上下文充分性评估**，并处理 **内容安全、Skills 相关性过滤、空能力循环** 等边界。  
- 前端 **流式问答** + 后端 **Celery 异步索引** + **Redis 缓存** + **Milvus/Qdrant 双后端向量库** 的可配置部署。

---

## 十二、文档维护

- 配置与默认值以 **`backend/app/core/config.py`** 为准；升级模型或 API 时请同步核对本文「第三节」「第六节」。  
- 若代码行为与本文冲突，**以代码为准**，并建议更新本节。

---

*文档生成自仓库当前实现梳理，用于简历与面试准备。*
