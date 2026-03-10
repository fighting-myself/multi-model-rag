"""
视频内容理解：直接调用 qwen3-vl-plus 等原生支持视频的 VL 模型理解整段视频，
无需 OpenCV 抽帧，支持至少 1 分钟及更长视频。
"""
import base64
import logging

from openai import AsyncOpenAI

from app.core.config import settings


def _mime_for_video_ext(ext: str) -> str:
    ext = (ext or "mp4").lower()
    if ext in ("mp4", "m4v"):
        return "video/mp4"
    if ext == "webm":
        return "video/webm"
    if ext == "mov":
        return "video/quicktime"
    return "video/mp4"


async def extract_text_from_video(content: bytes, file_type: str) -> str:
    """
    将视频整体交给 qwen3-vl-plus（原生支持视频）进行理解，
    返回「视频内容描述」供智能问答注入上下文。支持至少 1 分钟及更长视频。
    """
    if not content or len(content) == 0:
        return ""

    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    if not api_key:
        logging.warning("未配置 DASHSCOPE_API_KEY/OPENAI_API_KEY，无法调用视频理解")
        return "视频内容：未配置 API Key，无法解析。"

    mime = _mime_for_video_ext(file_type)
    b64 = base64.standard_b64encode(content).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.DASHSCOPE_BASE_URL or settings.OPENAI_BASE_URL,
    )
    model = settings.LLM_MODEL or "qwen3-vl-plus"

    prompt = (
        "请详细描述这段视频的内容（按时间顺序），包括：场景、人物、动作、出现的文字、关键事件等，用于后续问答检索。"
        "视频可能长达 1 分钟或更久，请尽量覆盖关键内容，输出一段连贯的描述文本，不要分条列举。"
    )

    try:
        # 百炼 VL 视频需用 video_url；图片用 image_url。支持 data:video/mp4;base64,... 或 https URL
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "video_url", "video_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            return "视频内容描述：模型未返回文字，请结合用户问题理解。"
        return "视频内容描述（按时间顺序）：\n\n" + raw
    except Exception as e:
        logging.warning("视频理解失败（qwen3-vl-plus）: %s", e)
        return "视频内容：解析失败（%s），请换用较短或标准格式（mp4/webm/mov）后重试。" % (str(e)[:80])
