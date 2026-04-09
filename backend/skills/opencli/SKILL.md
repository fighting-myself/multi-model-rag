---
name: opencli
description: "Use OpenCLI (@jackwener/opencli) to turn websites/Electron apps into CLIs, run explore→synthesize→generate pipelines, browser automation (operate), CLI Hub passthrough, plugins, and doctor/daemon troubleshooting. Use when the user mentions opencli, Website→CLI, explore/synthesize/record/generate/cascade, Browser Bridge extension, daemon port 19825, CLI-EXPLORER/CLI-ONESHOT/AGENT.md integration, or fixing extension-not-connected / exit 69."
---

# OpenCLI

[OpenCLI](https://github.com/jackwener/opencli) 把网站、Electron 应用或本机命令行工具统一成可脚本化 CLI；依赖 **Chrome + Browser Bridge 扩展** 与本机 **Daemon**（默认与 `localhost:19825` 通信）。凭证复用浏览器登录态。

## 前置

- **Node.js >= 20**；安装：`npm install -g @jackwener/opencli`。
- **扩展**：从 [Releases](https://github.com/jackwener/opencli/releases) 下载 `opencli-extension.zip`，解压后在 `chrome://extensions` 开启开发者模式 -> **加载已解压的扩展程序**。
- **诊断**：`opencli doctor` - Daemon、扩展、连通性须通过后再跑依赖浏览器的子命令。
- **内网站点**：先在 Chrome 中登录目标站（含 SSO）；再执行 `opencli` 相关命令。

## 何时优先用 OpenCLI

- 用户要把 **任意 URL** 做成自定义适配器（`explore` / `synthesize` / `generate`）。
- 需要 **浏览器自动化**（`operate` 系，供 Agent 控制页面）。
- 需要 **统一调用本机已有 CLI**（`opencli gh`、`opencli docker` 等 CLI Hub）。
- **排障**：扩展未连接、Daemon 未起、退出码 69（服务不可用）。

## 何时不要用

- 仅需 **REST/API 拉取** 且已有服务端凭证（例如本仓库 **confluence** 技能走 REST）- 不必强行走 OpenCLI 浏览器栈。
- 未装扩展或未通过 `doctor` 时，不要假设浏览器命令可用。

## 核心命令（简表）

| 目的 | 命令 |
|------|------|
| 健康检查 | `opencli doctor` |
| Daemon 状态 | `opencli daemon status` |
| 列出命令 | `opencli list` |
| 探索站点并落盘元数据 | `opencli explore <url> --site <slug>` |
| 生成适配器 | `opencli synthesize <slug>` |
| 一键：URL + 目标描述 | `opencli generate <url> --goal "..."`（细节以仓库 CLI-ONESHOT 为准） |
| 本机 CLI 注册到 Hub | `opencli register <name>` |
| 更新全局包 | `npm install -g @jackwener/opencli@latest` |

`explore` 产出通常位于项目或用户目录下的 **`.opencli/explore/<slug>/`**（含 `manifest.json`、`endpoints.json`、`auth.json`、`candidates/` 等）；合成/迭代前可对照这些文件排查。

## 上游 Agent 技能（可选）

安装到本机供 Claude/Codex 等使用：

```bash
npx skills add jackwener/opencli
npx skills add jackwener/opencli --skill opencli-operate
npx skills add jackwener/opencli --skill opencli-usage
```

在 Cursor 中可将 `opencli list` 与常用工作流写入 **AGENT.md** 或 **项目规则**，便于模型发现可用子命令。

## 退出码（排障）

| 码 | 含义 |
|----|------|
| 69 | Browser Bridge 未连接 - 先修扩展与 `doctor` |
| 77 | 需登录目标站点 |
| 75 | 超时，可重试 |

## 安全与隐私

- 浏览器会话留在本机 Chrome；勿在对话中粘贴 Cookie/令牌。
- 企业内网 URL 与 `.opencli` 内探索结果可能含敏感路径；分享日志前打码。

## 参考

- 仓库 README / README.zh-CN：<https://github.com/jackwener/opencli>
- 深度探索与鉴权策略：**CLI-EXPLORER.md**；快速单页：**CLI-ONESHOT.md**（均在仓库根目录）
