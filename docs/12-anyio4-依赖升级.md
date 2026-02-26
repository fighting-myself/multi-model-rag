# AnyIO 4 依赖升级说明（解决 MCP + openai/fastapi 冲突）

## 问题

- **MCP**（streamable_http）需要 **anyio>=4.5**（`create_memory_object_stream[Type]` 下标语法）。
- **sse-starlette** 需要 **anyio>=4.7**、**starlette>=0.49.1**。
- 当前环境里 **openai 1.3.7**、**fastapi 0.104.1** 要求 **anyio<4**，与上面冲突。

## 解决思路

统一到 **anyio 4.x**：升级 openai、fastapi、starlette 到支持 anyio 4 的版本。

## 推荐版本（在 requirements.txt 中调整）

在 `backend/requirements.txt` 里做如下修改或确认：

| 包 | 原约束（导致冲突） | 推荐约束 | 说明 |
|----|-------------------|----------|------|
| **openai** | 1.3.7 或 anyio<4 | **openai>=2.0.0** | 2.x 为 anyio>=3.5.0,<5，兼容 anyio 4 |
| **fastapi** | 0.104.1（带 anyio<4） | **fastapi>=0.130.0** | 0.109/0.115/0.120 仍要求 starlette<0.49，需 0.130+ 才支持 starlette 0.49 |
| **starlette** | 0.27.0 | **starlette>=0.49.1** | 满足 sse-starlette，且支持 anyio 4 |
| **anyio** | 4.5.0 | **anyio>=4.7.0** | 满足 MCP 与 sse-starlette |

## 操作步骤

1. **备份并编辑 `backend/requirements.txt`**

   将相关行改为（或新增/统一为）：

   ```text
   # 使用 anyio 4.x，与 MCP、sse-starlette 兼容（fastapi 需 >=0.130 才支持 starlette 0.49）
   openai>=2.0.0
   fastapi>=0.130.0
   starlette>=0.49.1
   anyio>=4.7.0
   ```

   若你当前是固定版本（如 `openai==1.3.7`、`fastapi==0.104.1`），改成上述下限版本即可。

2. **注意**：FastAPI 0.130+ 要求 **pydantic>=2.7.0**、**Python>=3.10**。若当前是 pydantic 2.5，需一并升级 pydantic。

3. **重新安装依赖**

   ```bash
   cd backend
   pip install -r requirements.txt -U
   ```

   若仍有冲突，可先单独升级再装全量：

   ```bash
   pip install -U "openai>=2.0.0" "fastapi>=0.130.0" "starlette>=0.49.1" "anyio>=4.7.0"
   pip install -r requirements.txt
   ```

4. **OpenAI 1.x → 2.x 兼容性**

   项目里已用 `from openai import AsyncOpenAI`，2.x 保留该 API，一般无需改代码。若之前用过 1.x 的 `openai.api_key = ...` 等全局写法，需改为通过 `AsyncOpenAI(api_key=...)` 等构造方式传参。

5. **验证**

   ```bash
   python -c "import anyio; print(anyio.__version__)"   # 应为 4.7+
   python -c "import openai; print(openai.__version__)" # 应为 2.x
   python -c "import fastapi; print(fastapi.__version__)"
   ```

## 若无法升级 FastAPI（例如被其他依赖锁死）

若必须保留 **fastapi 0.104** 和 **anyio 3.x**，则无法同时使用当前 MCP SDK 的 streamable HTTP（依赖 anyio 4 的下标语法）。只能二者选一：

- 要么升级 fastapi/starlette/anyio 以使用 MCP（推荐），  
- 要么暂时不接阿里云 MCP，或等 MCP 提供不依赖 anyio 4 的版本。
