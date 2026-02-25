"""
图片 OCR 服务：使用阿里百炼 qwen-vl-ocr 模型从图片中提取文本
支持 jpeg、jpg、png；原图由文件服务保存在 MinIO，此处仅做文本提取供 RAG 向量化。
"""
import base64
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import settings


def _mime_for_ext(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png":
        return "image/png"
    return "image/jpeg"


async def extract_text_from_image(content: bytes, file_type: str) -> str:
    """
    使用 qwen-vl-ocr 从图片中提取文本（OCR）。
    原图已由上传流程保存在 MinIO，此处只返回识别出的文本，供分块与向量化。

    Args:
        content: 图片原始字节
        file_type: 扩展名，如 jpeg、jpg、png

    Returns:
        识别出的文本，失败返回空字符串
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

    def _is_empty_or_invalid(t: str) -> bool:
        t = (t or "").strip()
        if not t:
            return True
        if t == "0" or (len(t) <= 2 and t.isdigit()):
            return True
        return False

    try:
        completion = await client.chat.completions.create(
            model=settings.OCR_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                        {"type": "text", "text": '''提取图片中的所有文字。如果没有文字，请描述图片的内容（场景、主体、颜色、风格等），用于检索。'''},
                    ],
                },
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        if not _is_empty_or_invalid(text):
            return text

        # 首轮返回空或 "0" 等无效内容时，用仅描述图像的兜底请求再试一次（无文字图片）
        fallback_prompt = (
            "请用一段话描述这张图片的内容（场景、主体、颜色、风格等），用于检索。"
            "直接以「图片内容描述：」开头输出，不要其他解释。"
        )
        completion2 = await client.chat.completions.create(
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
        text2 = (completion2.choices[0].message.content or "").strip()
        if not _is_empty_or_invalid(text2):
            return text2

        # 仍无有效描述时返回占位文案，避免知识库因「无文本」跳过该文件
        return "图片内容描述：图片无文字内容，请根据视觉信息检索。"
    except Exception as e:
        logging.warning("图片 OCR 失败: %s", e)
        return ""
