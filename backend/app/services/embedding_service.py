"""
嵌入服务：使用阿里云百炼 DashScope 多模态 API（qwen3-vl-embedding）获取文本/图片向量
"""
import asyncio
import base64
import logging
import httpx
from typing import List
from app.core.config import settings
from app.core.ops_metrics import inc_embedding_transport_retry

logger = logging.getLogger(__name__)


async def get_embedding_for_image(image_bytes: bytes, image_format: str = "jpeg") -> List[float]:
    """单张图片获取向量（与文本同一向量空间，支持图搜图、以文搜图）。"""
    if not image_bytes or len(image_bytes) == 0:
        default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
        return [0.0] * default_dim
    fmt = (image_format or "jpeg").lower().replace("jpg", "jpeg")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_data = f"data:image/{fmt};base64,{b64}"
    embeddings = await _get_multimodal_embeddings(contents=[{"image": image_data}])
    default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
    return embeddings[0] if embeddings else [0.0] * default_dim


async def get_embedding(text: str) -> List[float]:
    """单条文本获取向量。"""
    if not text.strip():
        # 空文本返回零向量（使用配置的维度）
        default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
        return [0.0] * default_dim
    
    embeddings = await get_embeddings([text])
    return embeddings[0] if embeddings else [0.0] * getattr(settings, "ZILLIZ_DIM", 1536)


async def _request_multimodal_embeddings(contents: list, default_dim: int) -> List[List[float]]:
    """调用 DashScope 多模态 embedding API，contents 为 [{"text": "..."}] 或 [{"image": "data:image/..."}]。"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
    headers = {
        "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": "qwen3-vl-embedding", "input": {"contents": contents}}
    t = httpx.Timeout(
        connect=settings.HTTP_CONNECT_TIMEOUT_SEC,
        read=settings.EMBEDDING_HTTP_TIMEOUT_SEC,
        write=min(60.0, settings.EMBEDDING_HTTP_TIMEOUT_SEC),
        pool=5.0,
    )
    extra = max(0, int(getattr(settings, "EMBEDDING_HTTP_RETRIES", 1)))
    for attempt in range(extra + 1):
        try:
            async with httpx.AsyncClient(timeout=t) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
            break
        except httpx.HTTPStatusError as e:
            logger.error("DashScope API HTTP 错误: %s - %s", e.response.status_code, e.response.text)
            raise ValueError(f"DashScope API 调用失败: {e.response.status_code} - {e.response.text}") from e
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            logger.warning("DashScope embedding 网络超时/连接错误 attempt %s/%s: %s", attempt + 1, extra + 1, e)
            if attempt >= extra:
                raise ValueError(f"DashScope API 调用失败: {e}") from e
            inc_embedding_transport_retry()
            await asyncio.sleep(0.2 * (attempt + 1))
        except Exception as e:
            logger.error("DashScope API 调用异常: %s", e)
            raise ValueError(f"DashScope API 调用失败: {e}") from e
    out = result.get("output", {})
    embeddings_list = out.get("embeddings", [])
    return [
        emb_data["embedding"] if "embedding" in emb_data else [0.0] * default_dim
        for emb_data in embeddings_list
    ]


async def _get_multimodal_embeddings(contents: list) -> List[List[float]]:
    """多模态 contents 列表，返回等长向量列表。"""
    default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
    if not contents:
        return []
    return await _request_multimodal_embeddings(contents, default_dim)


async def get_embeddings(texts: List[str]) -> List[List[float]]:
    """批量文本获取向量。
    
    使用 DashScope 多模态 API qwen3-vl-embedding，与图片向量同一空间。
    批量大小限制：20
    """
    if not texts:
        return []
    default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
    inputs = [t.strip()[:8192] if t and t.strip() else " " for t in texts]
    batch_size = 20
    all_embeddings = []
    for i in range(0, len(inputs), batch_size):
        batch_inputs = inputs[i : i + batch_size]
        contents = [{"text": text} for text in batch_inputs]
        batch_embeddings = await _get_multimodal_embeddings(contents)
        all_embeddings.extend(batch_embeddings)
    if not all_embeddings:
        return [[0.0] * default_dim] * len(texts)
    dim = len(all_embeddings[0])
    result = []
    for i in range(len(texts)):
        result.append(all_embeddings[i] if i < len(all_embeddings) else [0.0] * dim)
    return result
