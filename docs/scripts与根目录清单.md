# 根目录与 `scripts/` 清单（改造 A-2）

> **原则**：仓库根目录保持「项目入口 + 文档 + 前后端 + 技能」；个人/生成类脚本集中说明归属，避免与业务代码混淆。

## 1. 仓库根目录（建议保留）

| 路径 | 性质 | 说明 |
|------|------|------|
| `README.md` | 保留 | 项目总览与启动说明 |
| `docs/` | 保留 | 需求、架构、改造计划等 |
| `backend/` | 保留 | FastAPI 与异步任务 |
| `frontend/` | 保留 | Web 前端 |
| `skills/` | 保留 | OpenClaw 风格技能（各 skill 内可有 `scripts/invoke.py`） |
| `docker-compose.yml`、`docker-compose.local.yml` | 保留 | 本地/部署编排 |
| `.env.example` | 保留 | 环境变量模板（与 `backend/app/core/config.py` 对照补全） |
| `.gitignore` | 保留 | — |
| `data/` | 保留（按需） | 本地运行时数据，勿提交敏感内容 |
| `py312/` | 视团队约定 | 若为本地 Python 运行时目录，建议加入 `.gitignore` 或文档说明 |

## 2. 根目录「个人/杂项」文件（建议不纳入 CI）

| 路径 | 建议 |
|------|------|
| `resume.docx`、`resume.pdf`、`resume.txt` | 个人履历材料：可移入 `docs/personal/` 或本地忽略，避免与项目代码混放 |
| `RAG.pdf` | 若为学习资料：移入 `docs/assets/` 或同上 |
| `容灾复制.pdf`（若存在） | 同上 |

## 3. 项目根目录 `scripts/`（Python 生成脚本）

| 文件 | 作用 | 建议 |
|------|------|------|
| `generate_personal_resume.py` | 个人履历生成 | **保留在根 `scripts/`** 或迁入 `docs/tools/`，运行方式见脚本内说明 |
| `generate_project_resume.py` | 项目说明类生成 | 同上 |
| `generate_resume_qmjianli.py` | 简历相关生成 | 同上 |
| `generate_resume_with_pdf_skill.py` | 结合 PDF skill 的生成 | 同上 |

**说明**：此类脚本**不是**后端 API 依赖；**不**迁入 `backend/scripts/`（该目录用于 **数据库迁移 SQL**，见 `backend/scripts/README.md`），以免与 DBA 脚本混淆。

## 4. 其他 `scripts/` 位置

| 路径 | 说明 |
|------|------|
| `skills/*/scripts/invoke.py` | 各技能入口，**保留**，与根 `scripts/` 职责不同 |

## 5. 变更记录

| 日期 | 动作 |
|------|------|
| 2026-04-07 | 初版清单：分类根目录与 `scripts/`，明确与 `backend/scripts/` 区分 |
