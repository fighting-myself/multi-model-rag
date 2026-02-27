# AI 多模态智能问答助手

企业级 AI 多模态智能问答与自动化系统，支持多格式文档 RAG 问答、多模态检索、浏览器自动化与**电脑管家**（Computer Use），并可结合 `.skill` 技能与 MCP 工具综合完成任务。

## 功能特性

### 核心能力

- **多格式文档与 RAG**：支持 PDF、PPT、TXT、XLSX、DOCX、HTML、Markdown、ZIP、JPEG 等上传与解析，基于 RAG 的智能问答
- **多模态检索**：文本 + 图片混合检索，以文搜图、以图搜图
- **用户与计费**：用户认证、权限管理、计费中心、使用统计与仪表盘
- **审计与安全**：操作审计日志、敏感信息脱敏、文件安全校验

### 智能助手与技能

- **浏览器助手**：多 Agent + Playwright，根据指令在浏览器中执行操作（打开网页、登录、获取 Cookie、填表、总结页面等）
- **电脑管家**：视觉识别 + AI 决策 + 键鼠操作，像人一样看屏幕、移动鼠标、敲键盘，操作整机（任意软件、Windows 桌面等）；结合 `.skill` 技能综合解决问题
- **.skill 技能**：项目根目录 `.skill` 下可放置技能文档（单文件 `.md` 或目录 + README），浏览器助手与电脑管家按需扫描并加载使用文档，按描述调用对应工具（如保存到 data 目录）

### 扩展与部署

- **MCP 工具**：接入外部 MCP 服务，扩展 Agent 能力
- **向量库**：支持 Zilliz、Qdrant
- **对象存储**：MinIO
- **任务队列**：Celery + Redis
- **Docker / Kubernetes**：容器化与 K8s 部署支持

## 技术栈

### 前端

- React 18 + TypeScript
- Ant Design 5
- Vite
- Axios

### 后端

- FastAPI
- PostgreSQL / MySQL
- Redis
- Zilliz / Qdrant（向量数据库）
- MinIO（对象存储）
- Celery（异步任务）

### AI 与自动化

- **LangChain**：默认使用 LangChain 封装 LLM（`langchain-openai` ChatOpenAI）、RAG 生成链与浏览器助手 Agent（`create_tool_calling_agent` + `AgentExecutor`）。可通过 `USE_LANGCHAIN=False` 回退为原生 OpenAI 调用。
- **LLM**：OpenAI 兼容接口（如 Qwen、GPT、Claude），支持多轮对话与 function calling
- **视觉模型**：用于电脑管家截图分析（可配置 `VISION_MODEL`，默认与 `LLM_MODEL` 一致）
- **Embedding**：文本向量化（如 qwen3-vl-embedding、m3e-base）
- **Rerank / OCR**：可选 Rerank 与 OCR 模型（如阿里百炼）
- **浏览器自动化**：Playwright（需执行 `playwright install`）
- **桌面自动化**：pyautogui（电脑管家需在有图形界面的环境，如 Windows 桌面）

## 快速开始

**详细说明见 [环境与启动指南](./docs/08-环境与启动指南.md)。**

### 前置要求

- **Docker 方式（推荐）**：Docker 20.10+ 与 Docker Compose 2.0+
- **本地开发**：Python 3.11+、Node.js 18+，以及 PostgreSQL、Redis、向量库、MinIO

### 必须配置

在项目根目录创建 `.env`（可参考 `.env.example`），**至少配置**：

- `POSTGRES_PASSWORD`（或 MySQL 相关）：数据库密码
- 生产环境务必设置：`SECRET_KEY`、`JWT_SECRET_KEY`
- AI 能力：`OPENAI_API_KEY`、`OPENAI_BASE_URL`（或阿里等兼容接口），以及 `LLM_MODEL` 等

### 启动方式

**1. Docker 一键启动（推荐）**

```bash
cd multi-model-rag
docker-compose up -d --build
```

- 后端 API：http://localhost:8000  
- API 文档：http://localhost:8000/docs  
- MinIO 控制台：http://localhost:9001（用户名/密码见 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`）

数据库表在后端首次启动时自动创建。

**2. 本地启动**

- 数据库用 MySQL、模型用阿里云、存储用远程服务时，见 **[本地启动（MySQL与远程存储）](./docs/09-本地启动（MySQL与远程存储）.md)**。
- **前后端分别启动**：见 **[前后端启动步骤](./docs/10-前后端启动步骤.md)**。

**3. 本地开发简要步骤**

- **后端**：`cd backend` → `pip install -r requirements.txt`（首次）→ `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
  - **浏览器助手**：需在 backend 环境中执行一次 `playwright install`
  - **电脑管家**：需安装 `pyautogui`（已在 requirements.txt），且需在**有图形界面的环境**（如 Windows 桌面）运行
- **前端**：`cd frontend` → `npm install`（首次）→ `npm run dev`

前端默认 http://localhost:3000，会代理 `/api` 到后端 8000 端口。

## 项目结构

```
multi-model-rag/
├── .skill/                    # 技能文档目录（可选）
│   └── *.md 或 <name>/README.md   # 单文件技能或目录技能
├── backend/                   # 后端
│   ├── app/
│   │   ├── api/v1/            # API 路由（认证、文件、知识库、问答、计费、审计、MCP、浏览器助手、电脑管家等）
│   │   ├── core/              # 配置、数据库、健康检查
│   │   ├── models/            # 数据库模型
│   │   ├── schemas/           # Pydantic 模型
│   │   ├── services/          # 业务逻辑（RAG、LLM、skill_loader、steward_agent、computer_steward_agent、desktop_tools 等）
│   │   └── tasks/             # Celery 任务
│   ├── alembic/               # 数据库迁移
│   └── requirements.txt
├── frontend/                  # 前端
│   ├── src/
│   │   ├── components/        # 布局、错误边界等
│   │   ├── pages/             # 首页、文件、知识库、问答、多模态检索、计费、审计、MCP、浏览器助手、电脑管家等
│   │   ├── services/           # API 调用
│   │   └── stores/             # 认证、主题等状态
│   └── package.json
├── docs/                      # 文档
├── docker-compose.yml
└── README.md
```

## 技能与管家说明

### .skill 技能

- 在项目根目录下创建 `.skill` 目录，可放置：
  - **单文件技能**：`<name>.md`，首行 `# 标题` 为技能名，正文为描述（含工具名、参数、用法）
  - **目录技能**：`<name>/` 下放 `README.md` 或 `index.md` 或 `doc.md` 为主文档，其他 `.md` 会一并加载
- **浏览器助手**与**电脑管家**在 system prompt 中会注入「可用技能」摘要；需要某技能时先调用 `skill_load(skill_id)` 加载完整文档，再按文档使用对应工具（如 `file_write` 保存到 data 目录）。

### 浏览器助手

- 入口：前端「浏览器助手」→ 输入自然语言指令。
- 能力：启动无头浏览器，打开 URL、填表、点击、获取页面文本/Cookie 等，并可调用 `file_write` 与 `.skill` 技能。
- 依赖：Playwright，需执行 `playwright install`。

### 电脑管家

- 入口：前端「电脑管家」→ 输入任务目标。
- 能力：截取当前屏幕 → 视觉模型分析截图 → 决策下一步（点击、输入、滚动、按键等）→ 执行键鼠操作；可结合 `skill_list` / `skill_load` 使用 `.skill` 能力。
- 依赖：pyautogui，且需在**有图形界面的环境**（如 Windows 桌面）运行；视觉模型可通过 `VISION_MODEL` 配置，为空则使用 `LLM_MODEL`。

## 文档

- [需求分析](./docs/01-需求分析.md)
- [技术选型](./docs/02-技术选型.md)
- [系统架构设计](./docs/03-系统架构设计.md)
- [价格策略](./docs/04-价格策略.md)
- [部署方案](./docs/05-部署方案.md)
- [实施步骤记录](./docs/06-实施步骤记录.md)
- [项目总结](./docs/07-项目总结.md)
- [环境与启动指南](./docs/08-环境与启动指南.md)
- [本地启动（MySQL与远程存储）](./docs/09-本地启动（MySQL与远程存储）.md)
- [前后端启动步骤](./docs/10-前后端启动步骤.md)
- [优化方向建议](./docs/11-优化方向建议.md)
- [anyio4 依赖升级](./docs/12-anyio4-依赖升级.md)
- [项目完善策略与实施](./docs/13-项目完善策略与实施.md)

## LangChain 改造说明

项目已接入 LangChain，在保持原有 API 与行为的前提下：

- **配置**：`.env` 或环境中设置 `USE_LANGCHAIN=True`（默认）则启用 LangChain；设为 `False` 则使用原生 OpenAI 调用。
- **LLM**：`app.services.langchain_llm` 提供与 `llm_service` 一致的接口（`chat_completion`、`chat_completion_stream`、`chat_completion_with_tools`、`query_expand`、`expand_image_search_terms`），内部使用 `ChatOpenAI`（base_url + bind_tools）。
- **RAG**：`app.services.langchain_rag` 提供基于 LangChain 的 RAG 生成链（prompt + LLM），检索逻辑仍由 `ChatService._rag_context` 完成；问答流程通过 `llm_service.chat_completion` 统一走 LangChain（当 `USE_LANGCHAIN=True`）。
- **浏览器助手**：当 `USE_LANGCHAIN=True` 时，`run_steward` 会调用 `langchain_steward_agent.run_steward_langchain`，使用 `create_tool_calling_agent` + `AgentExecutor` 执行任务；若 LangChain/Agent 依赖缺失则自动回退到原有「消息循环 + tool_calls」实现。
- **电脑管家**：仍为「截图 → 视觉模型 + tool_calls → 键鼠执行」循环，其中 `chat_completion_with_tools` 在启用 LangChain 时已为 LangChain 实现。

依赖见 `backend/requirements.txt`：`langchain-core`、`langchain-openai`、`langchain`、`langchain-community`。

## 开发

### 后端

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate  |  Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

## 测试

- **后端**：`cd backend && pytest`
- **前端**：`cd frontend && npm test`

## 部署

- **Docker**：`docker-compose up -d`
- **Kubernetes**：`kubectl apply -f k8s/`（如有）

详见 [部署方案](./docs/05-部署方案.md)。

## 许可证

MIT License

## 贡献

欢迎提交 Issue 与 Pull Request。
