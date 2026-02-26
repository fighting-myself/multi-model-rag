# AI多模态智能问答助手

企业级AI多模态智能问答系统，支持PDF、PPT、TXT、XLSX、DOCX、HTML、MARKDOWN、ZIP、JPEG等多种文件格式的上传和智能问答。

## 功能特性

- 📄 多格式文件支持（PDF、PPT、TXT、XLSX、DOCX、HTML、MARKDOWN、ZIP、JPEG等）
- 🔍 基于RAG的智能问答
- 🖼️ 多模态支持（文本+图片）
- 👥 用户认证和权限管理
- 💰 灵活的计费系统
- 📊 使用统计和分析
- 🚀 高性能和可扩展架构
- 🐳 Docker容器化部署
- ☸️ Kubernetes支持

## 技术栈

### 前端
- React 18 + TypeScript
- Ant Design 5
- Vite
- Axios

### 后端
- FastAPI
- PostgreSQL
- Redis
- Qdrant（向量数据库）
- MinIO（对象存储）
- Celery（异步任务）

### AI模型
- Embedding模型：m3e-base / OpenAI text-embedding-3-large
- LLM模型：Qwen2.5 / GPT-4 / Claude
- OCR模型：PaddleOCR

## 快速开始

**详细说明见 [环境与启动指南](./docs/08-环境与启动指南.md)。**

### 前置要求

- **Docker 方式（推荐）**：仅需 Docker 20.10+ 与 Docker Compose 2.0+
- **本地开发**：Python 3.11+、Node.js 18+，以及 PostgreSQL、Redis、Qdrant、MinIO

### 必须配置

在项目根目录创建 `.env`（可复制 `.env.example`），**至少配置**：

- `POSTGRES_PASSWORD`：数据库密码（Docker 下数据库用户固定为 `rag_user`）
- 生产环境务必设置：`SECRET_KEY`、`JWT_SECRET_KEY`

### 启动方式

**1. Docker 一键启动（推荐）**

```bash
cd multi-model-rag
docker-compose up -d --build
```

- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs
- MinIO 控制台：http://localhost:9001（用户名/密码见 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`）

数据库表在**后端首次启动时自动创建**，无需执行 alembic。

**2. 本地启动（MySQL + 阿里模型 + 远程存储）**

数据库用 MySQL、模型用阿里云、存储用远程免费服务时，见 **[本地启动（MySQL与远程存储）](./docs/09-本地启动（MySQL与远程存储）.md)**。  
根目录 `.env` 已按 MySQL、阿里模型、MinIO Play 公网免费地址配置；Redis/Qdrant 需免费注册后填入。

**3. 前后端怎么启动（本地开发）**

详见 **[前后端启动步骤](./docs/10-前后端启动步骤.md)**。简要步骤：

- **后端**：`cd backend` → `pip install -r requirements.txt`（首次）→ `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- **前端**：`cd frontend` → `npm install`（首次）→ `npm run dev`

先启后端，再启前端；前端默认 http://localhost:3000，会代理 `/api` 到后端 8000 端口。

## 项目结构

```
multi-model-rag/
├── backend/                 # 后端代码
│   ├── app/
│   │   ├── api/            # API路由
│   │   ├── core/           # 核心配置
│   │   ├── models/         # 数据库模型
│   │   ├── schemas/        # Pydantic模型
│   │   ├── services/       # 业务逻辑
│   │   └── utils/          # 工具函数
│   ├── alembic/            # 数据库迁移
│   └── requirements.txt    # Python依赖
├── frontend/               # 前端代码
│   ├── src/
│   │   ├── components/     # React组件
│   │   ├── pages/          # 页面
│   │   ├── services/       # API服务
│   │   └── utils/          # 工具函数
│   └── package.json        # Node依赖
├── docs/                   # 文档
│   ├── 01-需求分析.md
│   ├── 02-技术选型.md
│   ├── 03-系统架构设计.md
│   ├── 04-价格策略.md
│   └── 05-部署方案.md
├── docker-compose.yml      # Docker Compose配置
└── README.md              # 项目说明
```

## 文档

详细文档请查看 `docs/` 目录：

- [需求分析](./docs/01-需求分析.md)
- [技术选型](./docs/02-技术选型.md)
- [系统架构设计](./docs/03-系统架构设计.md)
- [价格策略](./docs/04-价格策略.md)
- [部署方案](./docs/05-部署方案.md)

## 开发

### 后端开发

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 前端开发

```bash
cd frontend
npm install
npm run dev
```

## 测试

### 后端测试
```bash
cd backend
pytest
```

### 前端测试
```bash
cd frontend
npm test
```

## 部署

### Docker部署
```bash
docker-compose up -d
```

### Kubernetes部署
```bash
kubectl apply -f k8s/
```

详细部署说明请参考 [部署方案文档](./docs/05-部署方案.md)

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！

## 联系方式

- 项目地址：https://github.com/your-repo/multi-model-rag
- 问题反馈：https://github.com/your-repo/multi-model-rag/issues
