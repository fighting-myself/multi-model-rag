# 本地启动：MySQL + 阿里模型 + 远程存储

本文说明如何**在本地一键启动**项目：数据库用你提供的 **MySQL**，模型用 **阿里云百炼/DashScope**，Redis / Qdrant / MinIO 使用**远程免费服务**（MinIO 已配好公网免费地址，Redis 与 Qdrant 需你免费注册获取一次）。

---

## 一、已为你配置好的内容

### 1. 数据库：MySQL（远程）

- **地址**：`mysql7.sqlpub.com:3312`
- **库名**：`dbdrservice`
- **用户名**：`myroot123456`
- **密码**：已在 `.env` 中配置
- **连接串**：`mysql+asyncmy://myroot123456:****@mysql7.sqlpub.com:3312/dbdrservice?charset=utf8mb4`

后端已支持 MySQL（asyncmy），表会在**首次启动时自动创建**，无需手动建表或执行 alembic。

### 2. AI 模型：阿里云

- **OPENAI_BASE_URL**：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- **OPENAI_API_KEY**：已在 `.env` 中填写
- **LLM_MODEL**：`qwen3-vl-plus`
- **EMBEDDING_MODEL**：`text-embedding-3-large`

（如需改成其他百炼模型，只需改 `.env` 里的 `LLM_MODEL`。）

### 3. MinIO：公网免费（无需注册）

已使用 **MinIO 官方 Play 环境**，可直接用，无需本地安装 MinIO：

- **端点**：`play.min.io`（HTTPS）
- **Access Key / Secret Key**：已在 `.env` 中配置（MinIO 公开测试凭证）
- **注意**：该环境为公开测试用，**请勿存放敏感或重要数据**。

### 4. Redis / Qdrant：需你免费注册一次

- **Redis（含 Token 填写）**：用于会话/缓存/Celery。未配置时仅“后端 API”可正常跑，Celery 异步任务不可用。
  - **不要填 REST 的 https 地址**（如 `https://ample-krill-50902.upstash.io`），后端和 Celery 需要的是 **Redis 协议 URL**。
  - **Token/密码往哪填？** 不用单独填。在 Upstash 控制台里，选「Redis」连接方式（不是 REST），会看到 **Endpoint、Port、Password**；控制台通常有「Redis URL」一键复制，格式是：`rediss://default:你的密码@xxx.upstash.io:6379`，**密码已经包含在 URL 里**，把这一整串填到 `.env` 的 `REDIS_URL` 即可。
  - 免费获取： [Upstash](https://console.upstash.com) → 创建数据库 → 在「Connect to your database」里选 **Redis** 方式 → 复制「Redis URL」→ 填入 `.env` 的 `REDIS_URL`。
  - **Celery 有免费的远程地址吗？** 没有单独的“远程 Celery”服务。Celery 是跑在你本机/容器里的 worker，只是**消息队列用远程 Redis**。所以用同一个 Upstash Redis 即可：把 `REDIS_URL`、`CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND` 都填成**同一串 Redis URL**（Upstash 单库即可）。
- **向量库（Zilliz）**：用于向量检索（RAG）。已默认使用 **Zilliz Cloud**（免费）：在 `.env` 中配置 `VECTOR_DB_TYPE=zilliz`、`ZILLIZ_URI`、`ZILLIZ_TOKEN`、`ZILLIZ_COLLECTION_NAME`、`ZILLIZ_DIM=1536`。若改用 Qdrant，设 `VECTOR_DB_TYPE=qdrant` 并填 `QDRANT_URL`、`QDRANT_API_KEY`。

---

## 二、环境准备

- **Python 3.11+**
- 项目根目录已有 **`.env`**（已按你给的 MySQL、阿里模型、MinIO 写好；Redis/Qdrant 按上一步可选填写）。

---

## 三、启动方式

### 方式 A：本机直接跑后端（推荐先试）

在**项目根目录**执行（保证能读到根目录的 `.env`）：

```bash
cd e:\code\multi-model-rag\backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

或从项目根目录指定模块运行（同样会读根目录 `.env`）：

```bash
cd e:\code\multi-model-rag
set PYTHONPATH=backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- 后端地址：http://localhost:8000  
- 文档：http://localhost:8000/docs  
- 表会在**首次请求/启动时**在 MySQL 中自动创建。

### 方式 B：用 Docker 只跑后端

所有依赖（MySQL/Redis/Qdrant/MinIO）都从 `.env` 读，不启动本地 PostgreSQL/Redis 等容器：

```bash
cd e:\code\multi-model-rag
docker-compose -f docker-compose.local.yml up -d --build
```

- 访问同上：http://localhost:8000 、 http://localhost:8000/docs  
- 如需 Celery，在 `.env` 中配置好 `REDIS_URL` 后，可取消 `docker-compose.local.yml` 里 `celery-worker` 的注释并再次 up。

### 前端（可选）

```bash
cd e:\code\multi-model-rag\frontend
npm install
npm run dev
```

浏览器访问前端开发地址（一般为 http://localhost:5173），前端会把 `/api` 代理到本机 8000 端口。

---

## 四、配置小结

| 项目       | 说明 |
|------------|------|
| 数据库     | MySQL 远程：`dbdrservice` @ `mysql7.sqlpub.com:3312`，已在 `.env` 配置 |
| 模型       | 阿里云 DashScope，`OPENAI_*`、`LLM_MODEL` 已在 `.env` 配置 |
| MinIO      | 公网免费 Play：`play.min.io`，凭证已在 `.env` 配置 |
| Redis      | 需在 Upstash 免费获取 URL 后填入 `.env`（不填则无 Celery） |
| Qdrant     | 需在 Qdrant Cloud 免费获取 URL + API Key 后填入 `.env`（不填则 RAG 可能不可用） |

按上述任一种方式启动后，即可在本地使用「MySQL + 阿里模型 + 远程 MinIO」；补全 Redis/Qdrant 后即可完整使用异步任务与 RAG。
