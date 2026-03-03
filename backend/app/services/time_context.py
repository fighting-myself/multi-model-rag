"""
当前系统时间上下文：供 Agent / LLM 提示使用，避免模型使用自身训练数据中的「今天」导致日期判断错误。
"""
from datetime import datetime


def get_system_time_context() -> str:
    """返回当前系统日期时间的一段说明，用于拼入 system 或 user 提示。"""
    now = datetime.now()
    date_iso = now.strftime("%Y-%m-%d")
    date_cn = now.strftime("%Y年%m月%d日")
    time_cn = now.strftime("%H:%M")
    return (
        f"【重要】当前系统日期与时间（判断「今天」、预售期、是否过期等一律以此为准）："
        f"{date_cn} {time_cn}（ISO 日期 {date_iso}）。不要使用模型内部知识中的「当前」或年份，请以系统时间为准。"
    )
