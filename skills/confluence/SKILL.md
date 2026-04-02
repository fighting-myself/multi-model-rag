---
name: confluence
description: "Fetch pages from a server-configured document portal via REST (Basic auth). Use when: user shares links with portal-style paths (viewpage.action, pageId=, /wiki/, /pages/123) and needs summary or body. 优先使用 CONFLUENCE_* env；未配置时可在 skill_args 临时传入 base_url + 凭证。NOT for: generic sites (use web_fetch)."
---

# 文档门户（REST）

通过 **HTTP REST** 读取已配置门户中的页面与搜索（**Basic 认证**，与网页登录账号一致）。
凭证默认在服务端配置（避免在对话中传密码），但当服务端未配置时，本技能也支持在 `skill_args` 里临时传入 `base_url` 与凭证用于单次拉取。

适用于企业内常见的文档类门户部署，具体 URL 与路径以实际环境为准。

## 何时使用

- 用户粘贴 **文档门户页面链接**（路径中常见 `viewpage.action`、`pageId=`、`/wiki/`、`/pages/数字` 等）。
- 需要 **拉取正文、摘要、检索** 且已配置服务端 `CONFLUENCE_*`。

## 何时不要用

- 任意公网文章、非本集成覆盖的站点 → 用 **web_fetch**。
- 未配置环境变量 → 你可以提供 `skill_args` 临时凭证（或改用 web_fetch）。

## 服务端配置

### 用户名 + 密码（自建部署常见）

| 变量 | 说明 |
|------|------|
| `CONFLUENCE_BASE_URL` | 站点根 URL，无尾斜杠 |
| `CONFLUENCE_USERNAME` | 登录名 |
| `CONFLUENCE_PASSWORD` | 密码（仅 `.env`，勿提交仓库） |
| `CONFLUENCE_CONTEXT_PATH` | 若 REST 挂在子路径（如 `/confluence`）则填写，否则留空 |

### 邮箱 + API 令牌（云租户常见）

| 变量 | 说明 |
|------|------|
| `CONFLUENCE_BASE_URL` | 含租户路径的根地址（按实际为准） |
| `CONFLUENCE_EMAIL` | 账号邮箱 |
| `CONFLUENCE_API_TOKEN` | API 令牌 |

若同时配置两套，当前实现 **优先用户名+密码**。

## skill_invoke

`skill_id` 为 **`confluence`**。

- 默认使用：`{ "action": "get_page", "url": "…" }`（当用户提供 `viewpage.action?pageId=` / `/pages/数字` 这类链接时）
- `{ "action": "get_page", "url": "…" }`：取页面正文（HTML 会转为纯文本摘要）
- `{ "action": "get_page", "page_id": "…" }`：取页面正文（需同时提供 `base_url` 或可推断出的 `url`）
- `{ "action": "search", "query": "…" }` 或 `{ "action": "search", "cql": "…" }`：按 CQL/关键词搜索
- `{ "action": "check_auth" }`：仅用于排障（需确保 `base_url`/`url` 与凭证可用）

### `get_page` 入参约定

当服务端未配置 `CONFLUENCE_*` 时，可在 `skill_args` 里额外提供（用于单次拉取）：

- `base_url`：站点根地址（如 `https://confluence.aishu.cn`）
- `context_path`：若 REST 在子路径（如 `/confluence`）则填写，否则留空
- 自建部署凭证：`username` + `password`
- 云租户凭证：`email` + `api_token`
- `page_id` 兼容写法：`page_id` / `pageId` / `pageID`（由调用方传入时决定）

注意：若你只提供 `page_id`，但没有 `url`/`base_url`，技能无法推断 REST 根路径（会返回“未配置”错误）。
`url` 会用于自动推断 REST 根路径（兜底），因此在多数情况下直接传 `url` 最稳。

示例（仅演示字段名，勿在仓库提交明文密码）：

```json
{"action":"get_page","url":"https://your-host/pages/viewpage.action?pageId=123456","username":"<USERNAME>","password":"<PASSWORD>"}
```

## 安全

凭证只放在服务器环境；账号权限宜最小（只读即可）。

## 参考

- REST 路径与字段以实际部署的厂商文档为准。
