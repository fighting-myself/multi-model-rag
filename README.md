## AI 多模态智能问答与 RAG 指标平台

企业级 AI 多模态智能问答系统，支持多格式文档 RAG 问答、多模态检索与「RAG 六大指标」评测，集成浏览器助手、电脑管家、skills 技能与 MCP 工具，可在一个平台内完成知识问答、业务自动化与效果评估。

---

## 功能总览

### 知识库与问答

- **多格式文档入库与分块**：支持 PDF / PPT / TXT / XLSX / DOCX / HTML / Markdown / ZIP / 图片 等文件上传、解析与分块。
- **RAG 智能问答**：
  - 单库 / 多库选择，支持开启/关闭 RAG。
  - 向量 + 全文（BM25）**混合检索**，RRF 融合 + Rerank。
  - 支持 LlamaIndex 查询变换（Advanced RAG，多查询改写）。
  - 流式输出，对话历史自动拼接，返回命中片段作为溯源。

### 检索与多模态

- **文本检索**：基于向量库与 BM25 的混合检索。
- **多模态检索**：ImageSearch 页面支持以文搜图、以图搜图。

### RAG 评测与观测

- **召回率评测（Recall Evaluation）**：
  - 支持配置 benchmark（query + 相关 chunk / 关键词）。
  - 计算 Recall@k、Hit@k、MRR，并输出逐题详情。
  - 检索端支持 vector / fulltext / hybrid，多种 top_k 组合。
- **RAG 六大指标页面（Advanced RAG Metrics）**：
  - **答案准确率（Accuracy）**：批量一次 LLM 调用 + 统一判分。
  - **召回率（Recall）**：按默认 benchmark + 当前知识库计算 Recall@1/3/5/10。
  - **检索精准度（Precision）**：基于召回详情计算 Precision@k。
  - **延迟（Latency）**：多次流式请求统计 TTFT 与端到端耗时。
  - **幻觉率（Hallucination）**：批量问题一次 LLM 调用 + 本地规则判是否幻觉。
  - **QPS / 并发能力**：多协程并发 chat_stream，统计平均延迟与失败率。
  - 支持**一键评测**与单项评测，前端对每一项设置了合理的超时时间。

### 助手与自动化

- **浏览器助手**：基于 Playwright 的浏览器自动化，支持打开网页、登录、填表、抓取页面内容等。
- **电脑管家（Computer Use）**：截图 + 视觉模型 + 键鼠控制，实现「像人一样看屏幕、点鼠标、敲键盘」的桌面自动化。
- **skills 技能（OpenClaw 风格）**：
  - `skills/<name>/SKILL.md` 定义技能说明与工具用法（支持 YAML frontmatter）。
  - 浏览器助手与电脑管家可按需加载技能文档并调用对应工具。
- **MCP 工具**：可接入外部 MCP server，将更多系统能力暴露为工具。

### 安全、运营与基础能力

- **用户与认证**：JWT 登录、用户管理。
- **计费与用量统计**：调用次数、token 用量等监控（如启用相关模块）。
- **审计日志**：对关键操作进行审计记录。
- **文件安全与脱敏**：上传文件的内容检查与敏感信息脱敏（视配置而定）。

---

## 技术栈

### 后端

- **框架**：FastAPI（异步）、SQLAlchemy 2.0 + AsyncSession、Pydantic 2.x。
- **数据库**：PostgreSQL / MySQL（通过 `asyncmy`）。
- **缓存 / 队列**：Redis、Celery（可选）。
- **向量数据库**：Zilliz Cloud / Milvus 兼容，或 Qdrant。
- **对象存储**：MinIO。
- **RAG / LLM**：
  - 自研 `ChatService`：封装检索、上下文构造、对话历史、联网检索、工具调用等。
  - `advanced_rag_service`：基于 LlamaIndex 的 Advanced RAG（多查询改写）。
  - `llm_service`：统一的 LLM 封装，走 OpenAI 兼容接口（可接 Qwen、GPT 等）。
  - `embedding_service`：文本 / 多模态向量化（如 `qwen3-vl-embedding`）。
  - 支持 **LangChain** 作为可选实现（RAG 链、工具调用等），可通过环境变量开关。

### 前端

- **框架**：React 18 + TypeScript。
- **构建**：Vite。
- **UI**：Ant Design 5。
- **数据请求**：Axios，统一 `api` 封装。
- **状态管理**：Zustand 等（按模块划分）。
- **可视化**：ECharts（RAG 指标展示）。

---

## 环境准备与配置

### 前置要求

- Python 3.11+
- Node.js 18+
- 数据与基础设施：
  - MySQL / PostgreSQL
  - Redis
  - 向量数据库（Zilliz / Qdrant 等）
  - MinIO（或兼容对象存储）

### 必要环境变量（示例）

在项目根目录创建 `.env`（可参考 `.env.example`），常用关键项包括：

- **数据库与缓存**
  - `DATABASE_URL`：数据库连接字符串，例如  
    `mysql+asyncmy://user:password@127.0.0.1:3306/multi_model_rag`
  - `REDIS_URL`：Redis 连接，例如 `redis://127.0.0.1:6379/0`
- **向量库**
  - `VECTOR_DB_TYPE`：`zilliz` / `qdrant`
  - `ZILLIZ_URI`、`ZILLIZ_TOKEN` 或 `QDRANT_URL`、`QDRANT_API_KEY`
- **对象存储**
  - `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`
- **LLM / Embedding**
  - `OPENAI_API_KEY`、`OPENAI_BASE_URL`
  - `LLM_MODEL`（如 `qwen3-vl-plus`）
  - `EMBEDDING_MODEL`（如 `qwen3-vl-embedding`）
- **安全相关**
  - `SECRET_KEY`、`JWT_SECRET_KEY`（生产环境务必自定义）
- **RAG 行为控制（节选）**
  - `USE_LANGCHAIN`：是否开启 LangChain 实现（`True` / `False`）
  - `USE_ADVANCED_RAG`：是否开启 LlamaIndex 查询变换
  - `RAG_CONFIDENCE_THRESHOLD`：低于该置信度时才返回检索上下文
  - `RAG_USE_BM25`：是否启用 BM25 全文检索

更多配置可在 `backend/app/core/config.py` 中查看。

---

## 本地启动

### 1. 启动后端

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate
# macOS / Linux: source venv/bin/activate
pip install -r requirements.txt
# 若需测试与代码质量工具（pytest/black/flake8/mypy），改用：
# pip install -r requirements-dev.txt

# 开发模式启动
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> 说明：`backend/Dockerfile` 使用 `constraints.txt`、BuildKit pip 缓存，以及 `requirements-heavy.txt`（重依赖独立缓存层）来降低依赖回溯与重复下载耗时。

后端与 Agent 相关的公共约定（避免散落在各 service）：

- `app/core/exceptions.py`：单智能体 / 多智能体可预期失败异常（API 映射 502 等）
- `app/core/constants.py`：重试次数、CrewAI/LiteLLM、轨迹字段等常量
- `app/prompts/`：单智能体系统提示与 CrewAI 场景模板（与 `app/schemas` 中的场景枚举对齐）

- 后端默认监听 `http://localhost:8000`
- OpenAPI 文档：`http://localhost:8000/docs`
- 部分功能（如浏览器助手、电脑管家）需要额外依赖：
  - 浏览器助手：在 backend 虚拟环境中执行一次 `playwright install`
  - 电脑管家：依赖 `pyautogui`，需在有图形界面的环境（如 Windows 桌面）运行

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

- 前端默认：`http://localhost:6006`（以实际 `vite.config.ts` 为准）
- 开发环境下 `/api` 会被代理到 `http://localhost:8000`

### 3. 可选：启动 Celery 任务队列

若需要在后台异步执行长任务（如大批量文件处理），可以启动 Celery：

```bash
cd backend
celery -A app.celery_app worker -l info
```

---

## 目录结构

```text
multi-model-rag/
├── backend/
│   ├── app/
│   │   ├── api/v1/                 # API 路由
│   │   │   ├── auth.py             # 认证 / 用户
│   │   │   ├── files.py            # 文件上传 / 管理
│   │   │   ├── knowledge_bases.py  # 知识库管理
│   │   │   ├── chat.py             # 问答接口
│   │   │   ├── evaluation.py       # 召回率与 RAG 六大指标
│   │   │   ├── search.py           # 检索与多模态检索
│   │   │   ├── steward.py          # 浏览器助手
│   │   │   ├── computer_steward.py # 电脑管家
│   │   │   └── ...
│   │   ├── core/                   # 配置、数据库、健康检查
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   └── health.py
│   │   ├── models/                 # SQLAlchemy ORM 模型（User / File / Chunk / KnowledgeBase 等）
│   │   ├── schemas/                # Pydantic 模型（请求 / 响应）
│   │   ├── services/               # 业务逻辑
│   │   │   ├── chat_service.py             # 问答主流程（RAG、工具、联网等）
│   │   │   ├── advanced_rag_service.py     # LlamaIndex Advanced RAG
│   │   │   ├── recall_evaluation_service.py# 召回率评测
│   │   │   ├── rag_metrics_service.py      # RAG 六大指标评测
│   │   │   ├── vector_store.py             # 向量库封装
│   │   │   ├── embedding_service.py        # 向量模型封装
│   │   │   ├── llm_service.py              # LLM 统一调用
│   │   │   ├── knowledge_base_service.py   # 知识库相关操作
│   │   │   └── ...
│   │   └── tasks/                  # Celery 任务（如 KB 相关异步任务）
│   ├── run.py                      # 入口脚本
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Chat.tsx                   # 问答页面
│   │   │   ├── KnowledgeBases.tsx         # 知识库管理
│   │   │   ├── RecallEvaluation.tsx       # 召回率评测
│   │   │   ├── AdvancedRAGMetrics.tsx     # RAG 六大指标
│   │   │   ├── ImageSearch.tsx            # 多模态检索
│   │   │   └── ...
│   │   ├── services/                      # 前端 API 调用封装
│   │   ├── components/                    # 公共组件、布局
│   │   └── stores/                        # 全局状态（认证、配置等）
│   └── vite.config.ts
├── skills/                        # skills 技能目录（若使用）
├── docs/                          # 设计与部署文档
└── README.md
```

---

## RAG 流程与评测简介

### 问答数据流（简要）

1. 前端 `Chat` 页面发送问题与所选知识库 ID。
2. 后端 `ChatService`：
   - 根据配置决定是否启用 RAG、是否使用 Advanced RAG/LlamaIndex。
   - 在向量库 + BM25 中检索候选片段，使用 RRF + Rerank 融合结果。
   - 结合对话历史与（可选）联网检索结果构造上下文。
3. 通过 `llm_service` 调用 LLM，生成回答。
4. 返回回答、置信度与命中的片段信息，前端做可视化展示。

### RAG 六大指标评测（简要）

- 评测集由 `backend/data/rag_default_benchmarks.json` 或 `rag_metrics_defaults.py` 提供。
- 后端的 `rag_metrics_service.py` 与 `recall_evaluation_service.py` 负责：
  - 并发检索 / 并发构造上下文。
  - 使用固定输入/输出格式一次调用 LLM（批量问题统一回答）。
  - 在本地对每条结果做判分与指标聚合。
- 前端 `AdvancedRAGMetrics.tsx` 提供一键评测界面与可视化展示。

---

## 构建镜像
- 前端镜像：
  cd multi-model-rag/frontend
  docker build -t rag-frontend:v1 .
- 后端镜像：
  cd multi-model-rag/backend
  DOCKER_BUILDKIT=1 docker build -t rag-backend:v1 .

## 开发、测试与部署

- **后端开发**：见「本地启动」小节命令。
- **前端开发**：`cd frontend && npm install && npm run dev`。
- **测试**：
  - 后端：`cd backend && pytest`（若已编写测试）。
  - 前端：`cd frontend && npm test`（若已配置测试脚本）。
- **部署**：
  - 可通过 Docker / K8s 等方式部署（参考 `docs/05-部署方案.md` 与你当前的实际部署脚本）。
  
  容器部署（前端 + 后端 + MinIO，同一网络）：
  1. 创建网络：
     `docker network create rag-net`
  2. 准备宿主机目录并启动 MinIO（API:9000 / 控制台:9001）：
     - `mkdir -p /data/minio`
     - `docker run -d --name minio --restart always --network rag-net -p 9000:9000 -p 9001:9001 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 -v /data/minio:/data minio/minio server /data --console-address ":9001"`
  3. 登录 MinIO 控制台 `http://<你的IP>:9001`，创建 bucket：`rag-files`
  4. 后端配置（`backend/.env`）至少包含：
     - `MINIO_ENDPOINT=minio:9000`
     - `MINIO_ACCESS_KEY=minioadmin`
     - `MINIO_SECRET_KEY=minioadmin123`
     - `MINIO_SECURE=false`
     - `MINIO_BUCKET_NAME=rag-files`
  5. 启动后端：
     `docker run -d --name backend --restart always --network rag-net -p 8000:8000 -v /etc/localtime:/etc/localtime:ro rag-backend:v1`
  6. 启动前端：
     `docker run -d --name rag-frontend --restart always --network rag-net -p 80:80 -v /etc/localtime:/etc/localtime:ro rag-frontend:v1`
  7. 验证：
     - 前端：`curl -I http://127.0.0.1`
     - 后端：`curl -I http://127.0.0.1:8000/live`
     - MinIO：`curl -I http://127.0.0.1:9000/minio/health/live`
  
  说明：
  - 若后端不在容器中运行（本地 Python），`MINIO_ENDPOINT` 请改为 `127.0.0.1:9000`。
  - 生产环境建议改掉默认 `minioadmin/minioadmin123`。
  

---

## 许可证与贡献

- 本项目使用 MIT License（如需，可在根目录更新 LICENSE 文件）。
- 欢迎提 Issue 与 Pull Request，一起完善 RAG 流程与评测能力。