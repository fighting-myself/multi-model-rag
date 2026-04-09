---
name: opencli-confluence-aishu
description: "Use OpenCLI to operate and CLI-ify https://confluence.aishu.cn for page search/read workflows. Trigger when user asks to use OpenCLI for Aishu Confluence, wants explore/record/synthesize for that domain, or needs browser-driven Confluence commands in Cursor skills."
---

# OpenCLI Confluence Aishu

用 OpenCLI 把 `https://confluence.aishu.cn/` 变成可复用 CLI 工作流。  
当前仓库已跑过一次：

- `opencli explore https://confluence.aishu.cn/ --site confluence-aishu`
- `opencli synthesize confluence-aishu`

但结果为 `candidate_count = 0`，说明仅首页探索没抓到可直接复用的 API 候选，下一步应走 **record + operate** 路线。

## 固定流程（按顺序）

1. **健康检查**
   - `opencli doctor`
   - 期望：Daemon/Extension/Connectivity 全部 OK。

2. **先在 Chrome 手工登录**
   - 打开 `https://confluence.aishu.cn/`，确保 SSO 登录成功并能访问空间。

3. **定向探索**
   - `opencli explore https://confluence.aishu.cn/ --site confluence-aishu`
   - 查看产物：`.opencli/explore/confluence-aishu/`

4. **录制真实交互（关键）**
   - `opencli record https://confluence.aishu.cn/ --site confluence-aishu`
   - 在浏览器中执行目标动作：搜索关键词、打开页面、翻页、进入空间列表。
   - 录制结束后检查：`.opencli/explore/confluence-aishu/candidates/candidates.json`

5. **合成候选命令**
   - `opencli synthesize confluence-aishu`
   - 若仍为 0，继续补录制，直到出现 candidates。

6. **验证与执行**
   - `opencli operate open https://confluence.aishu.cn/`
   - `opencli operate state`
   - `opencli operate click <index>`
   - `opencli operate type <index> <text>`
   - `opencli operate wait time 2`
   - `opencli operate get`
   - `opencli operate network --limit 100`
   - `opencli operate close`

## 连接信息来源（必须）

- 如果命令或技能需要账号/密码/Cookies，不要在提示词里硬编码。
- 统一从「外接平台连接信息」读取，使用 `connection_name` 指向配置项。
- 建议在调用 `skill_invoke` 时传：
  - `skill_id: "opencli-confluence-aishu"`
  - `skill_args.connection_name: "confluence.aishu.cn"`
  - 其余参数只放业务字段（如 `action`、`url`、`query`）。

注意：本技能本质是 OpenCLI 浏览器会话方案，`connection_name` 主要用于上层统一配置提示；实际是否能读到内容取决于本机 OpenCLI/浏览器登录态与页面权限。

## 首批已沉淀命令

已在本机 OpenCLI 适配器目录生成：

- `C:\Users\Liqu.li\.opencli\clis\confluence-aishu\search.ts`
- `C:\Users\Liqu.li\.opencli\clis\confluence-aishu\page.ts`
- `C:\Users\Liqu.li\.opencli\clis\confluence-aishu\spaces.ts`

可直接调用：

- `opencli confluence-aishu spaces --limit 20 -f json`
- `opencli confluence-aishu search --query "RAG" --limit 10 -f json`
- `opencli confluence-aishu page --url "https://confluence.aishu.cn/pages/viewpage.action?pageId=123456" -f json`

说明：当前环境下 `spaces` 与 `search` 能跑通，但可能返回空数组（与登录态/权限相关）；`page` 需要可访问页面的真实 `pageId`。

## skill_invoke 动作（已扩展）

`skill_id`：`opencli-confluence-aishu`

- `{"action":"get_page","url":"..."}`  
  通用取页。支持 `viewpage.action?pageId=`、`/pages/数字`、`/display/...`（会自动回退浏览器抓正文）。
- `{"action":"get_display_page","url":"https://confluence.aishu.cn/display/..."}`  
  强制走浏览器抓取，适合不带 `pageId` 的 URL。
- `{"action":"extract_links","url":"...","limit":100}`  
  提取页面可见链接（文本+URL）。
- `{"action":"list_demands","url":"..."}` / `{"action":"version_demands","url":"..."}`  
  从版本页正文中识别需求条目（需求号+标题+推导链接）。
- `{"action":"oracle_demands","url":"..."}`  
  从已识别需求中筛出 Oracle 相关条目并给链接。
- `{"action":"demands_with_filter","url":"...","keywords":["oracle","mysql"]}`  
  按关键词筛选需求（支持字符串或数组）。
- `{"action":"demands_json","url":"..."}`  
  以 JSON 返回需求列表（id/title/url）。
- `{"action":"oracle_demands_json","url":"..."}`  
  以 JSON 返回 Oracle 相关需求。
- `{"action":"group_demands_by_db","url":"..."}`  
  按数据库类型分组返回需求（oracle/mysql/sqlserver/kingbase/goldendb/oceanbase/dameng/other）。
- `{"action":"dedupe_by_id","url":"..."}` / `{"action":"dedupe_by_id","urls":["...","..."]}`  
  对单页或多页需求按需求号去重后返回文本列表。
- `{"action":"export_csv","url":"..."}` / `{"action":"export_csv","urls":["...","..."]}`  
  导出 CSV（id,title,url）。
- `{"action":"follow_links","url":"...","limit":5}`  
  自动跟进前 N 条需求链接，抓取每条页面摘要（用于补充状态/细节）。
- `{"action":"validate_links","url":"...","limit":10}`  
  校验需求链接可访问性（返回 JSON：ok/not_found_or_no_permission/fetch_failed）。
- `{"action":"export_markdown","url":"...","title":"Zeus-M7 需求清单"}`  
  导出 Markdown 汇报格式；也支持 `urls` 多页合并导出。
- `{"action":"debug_page","url":"..."}`  
  输出 open/result/current_url/title/text_head，用于排障。
- `{"action":"search","query":"RAG","limit":10}`
- `{"action":"spaces","limit":20}`
- `{"action":"check_auth"}`

### 典型调用（版本需求页）

- 列全量需求：  
  `{"action":"list_demands","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0"}`
- 列 Oracle 需求：  
  `{"action":"oracle_demands","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0"}`
- 列 Oracle + MySQL（关键词过滤）：  
  `{"action":"demands_with_filter","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0","keywords":["oracle","mysql"]}`
- 导出 CSV：  
  `{"action":"export_csv","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0"}`
- 跟进前 3 条需求链接：  
  `{"action":"follow_links","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0","limit":3}`
- 校验前 10 条需求链接可访问性：  
  `{"action":"validate_links","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0","limit":10}`
- 导出 Markdown 汇总：  
  `{"action":"export_markdown","url":"https://confluence.aishu.cn/display/DMS/Zeus-M7-8.0.9.0","title":"Zeus-M7-8.0.9.0 需求清单"}`

## 推荐目标命令（优先沉淀）

- 搜索知识页（按关键词）
- 打开指定页面并提取标题/正文
- 列出当前可见空间
- 导出最近访问或最近更新页面列表

## 故障排查

- `exit 69`：扩展未连接，先修 `opencli doctor`。
- 能打开首页但无候选：通常是没有在 `record` 里执行足够动作。
- 只看到 HTML 无接口：加长录制，覆盖搜索、详情、分页、侧边栏操作。

## 与现有 confluence REST 技能的关系

- 本技能是 **OpenCLI 浏览器方案**（复用登录态，适配内网 SSO）。
- `skills/confluence` 是 **REST 方案**（依赖服务端凭证）。
- 优先级建议：
  - 需要稳定结构化数据 + 有服务端凭证：用 `confluence`。
  - 需要复用浏览器登录态、处理复杂前端行为：用 `opencli-confluence-aishu`。
