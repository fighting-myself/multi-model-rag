"""
图片 OCR 服务：用 LLM 从图片中提取文字；若无文字则让 LLM 描述图片。
目标：返回一段用于检索的文本（有字则 OCR 结果，无字则描述），供后续分块与向量化。
"""
import base64
import logging
import re

from openai import AsyncOpenAI

from app.core.config import settings


def _mime_for_ext(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png":
        return "image/png"
    return "image/jpeg"


def _normalize_image_description(raw: str) -> str:
    """
    将 LLM 返回的图片描述归一为「单段、不重复」的一段文字。
    模型可能：多行重复、同一行内用句号重复同一段。
    """
    if not raw or not raw.strip():
        return ""
    t = raw.strip()
    # 1) 纯「没有文字」类短句视为无效
    no_text_keywords = ("没有文字", "无文字", "图中没有", "图片中没有", "无文字内容", "不含文字")
    if any(kw in t for kw in no_text_keywords) and len(t) < 80:
        return ""
    # 2) 同一行内「图片内容描述：」出现多次 → 只保留第一段
    marker = "「图片内容描述：」"
    if marker in t:
        idx2 = t.find(marker, len(marker))
        if idx2 != -1:
            t = t[:idx2].rstrip().rstrip("。") + "。"
    marker2 = "图片内容描述："
    if marker2 in t and marker not in t:
        idx2 = t.find(marker2, len(marker2))
        if idx2 != -1:
            t = t[:idx2].rstrip().rstrip("。") + "。"
    # 3) 多行且都以图片描述开头 → 只保留第一行
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) >= 2:
        for prefix in ("「图片内容描述：」", "图片内容描述："):
            if lines[0].startswith(prefix) and all(ln.startswith(prefix) for ln in lines):
                return lines[0]
    # 4) 按句号拆成句，去重：内容完全相同的句只保留一句
    parts = re.split(r"[。！？]+", t)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return t
    unique = []
    for p in parts:
        if not p:
            continue
        if any(p.strip() == u.strip() for u in unique):
            continue
        unique.append(p)
    if not unique:
        return t
    if len(unique) == 1:
        s = unique[0]
        return s if s.endswith("。") else s + "。"
    joined = "。".join(unique)
    return joined if joined.endswith("。") else joined + "。"


async def extract_text_from_image(content: bytes, file_type: str) -> str:
    """
    用 LLM：有字则提取图中文字，无字则描述图片。
    返回一段用于检索的文本，保证语义单段、不重复。
    """
    if not content or len(content) == 0:
        return ""

    mime = _mime_for_ext(file_type)
    b64 = base64.standard_b64encode(content).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    if not api_key:
        logging.warning("未配置 DASHSCOPE_API_KEY/OPENAI_API_KEY，跳过图片 OCR")
        return ""

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.DASHSCOPE_BASE_URL or settings.OPENAI_BASE_URL,
    )

    prompt = (
        "请根据图片内容完成以下其一（只输出结果，不要解释）：\n"
        "1. 若图中有文字：提取图中全部文字，并简要说明文字所在位置或含义。\n"
        "2. 若图中没有文字：用一段话描述图片（场景、主体、颜色、风格等），便于后续检索。\n"
        "要求：只输出一段文字，不要重复同一段内容，不要输出「图中没有文字」等无效句。"
    )

    try:
        completion = await client.chat.completions.create(
            model=settings.OCR_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        )
        raw = (completion.choices[0].message.content or "").strip()
        logging.warning("[OCR] 首轮 raw 长度=%d 前100字=%r", len(raw), (raw[:100] if raw else ""))
        if not raw:
            return "图片内容描述：纯图无文字，请根据视觉信息检索。"
        out = _normalize_image_description(raw)
        logging.warning("[OCR] 归一化后 长度=%d 前100字=%r", len(out), (out[:100] if out else ""))
        if out:
            return out
        # 兜底：仍像「无文字」时返回单句描述
        fallback_prompt = "请用一句话描述这张图片的内容（场景、主体、颜色等），用于检索。不要重复句子。"
        comp2 = await client.chat.completions.create(
            model=settings.OCR_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": fallback_prompt},
                    ],
                },
            ],
        )
        raw2 = (comp2.choices[0].message.content or "").strip()
        logging.warning("[OCR] 兜底 raw2 长度=%d 前100字=%r", len(raw2), (raw2[:100] if raw2 else ""))
        out2 = _normalize_image_description(raw2) if raw2 else ""
        logging.warning("[OCR] 兜底归一化后 长度=%d 返回=%r", len(out2), (out2[:80] if out2 else ""))
        return out2 if out2 else "图片内容描述：纯图无文字，请根据视觉信息检索。"
    except Exception as e:
        logging.warning("图片 OCR 失败: %s", e)
        return ""
