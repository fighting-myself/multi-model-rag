---
name: memory
description: "任务前检索用户历史记忆、任务后写入关键信息，实现上下文延续。使用 memory_search / memory_get / memory_store。"
---

# memory（记忆）

在任务前检索用户历史记忆、在任务后写入关键信息，实现「之前做过什么」「继续处理昨天的文件」等上下文延续。

- **memory_search(query, max_results?)**：按关键词在用户记忆中检索，返回匹配片段（id、content、created_at）。回答「之前做过什么」「用户偏好」前可先调用。
- **memory_get(memory_id? 或 related_task_id?)**：根据 memory_search 返回的 id 或关联任务 id 读取单条完整记忆。
- **memory_store(memory_type, content, related_task_id?)**：将本次任务的关键信息写入记忆。memory_type 建议：task_context / user_preference / execution_record。任务结束后可调用以便后续延续。

当系统启用记忆（MEMORY_ENABLED）时，上述工具可用。
