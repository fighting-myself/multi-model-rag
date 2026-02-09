"""
嵌入服务：使用阿里云百炼 DashScope 原生 API 获取文本向量
"""
import httpx
from typing import List
from app.core.config import settings


async def get_embedding(text: str) -> List[float]:
    """单条文本获取向量。"""
    if not text.strip():
        # 空文本返回零向量（使用配置的维度）
        default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
        return [0.0] * default_dim
    
    embeddings = await get_embeddings([text])
    return embeddings[0] if embeddings else [0.0] * getattr(settings, "ZILLIZ_DIM", 1536)


async def get_embeddings(texts: List[str]) -> List[List[float]]:
    """批量文本获取向量。
    
    使用 DashScope 原生 API：https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding
    模型：qwen3-vl-embedding
    批量大小限制：20
    """
    if not texts:
        return []
    
    import logging
    default_dim = getattr(settings, "ZILLIZ_DIM", 1536)
    
    # 过滤空文本，空位用零向量补
    inputs = [t.strip()[:8192] if t and t.strip() else " " for t in texts]
    
    # DashScope qwen3-vl-embedding 批量大小限制为 20
    batch_size = 20
    all_embeddings = []
    
    # 分批处理
    for i in range(0, len(inputs), batch_size):
        batch_inputs = inputs[i:i + batch_size]
        
        # 构建请求体：将文本转换为 DashScope 格式
        contents = [{"text": text} for text in batch_inputs]
        
        # 使用 DashScope 原生 API
        url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
        headers = {
            "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "qwen3-vl-embedding",
            "input": {
                "contents": contents
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                
                # 解析响应
                if "output" in result and "embeddings" in result["output"]:
                    batch_embeddings = []
                    for emb_data in result["output"]["embeddings"]:
                        if "embedding" in emb_data:
                            batch_embeddings.append(emb_data["embedding"])
                        else:
                            batch_embeddings.append([0.0] * default_dim)
                    all_embeddings.extend(batch_embeddings)
                else:
                    logging.error(f"DashScope API 响应格式错误: {result}")
                    # 如果响应格式错误，用零向量填充
                    all_embeddings.extend([[0.0] * default_dim] * len(batch_inputs))
                    
        except httpx.HTTPStatusError as e:
            logging.error(f"DashScope API HTTP 错误: {e.response.status_code} - {e.response.text}")
            raise ValueError(f"DashScope API 调用失败: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logging.error(f"DashScope API 调用异常: {e}")
            raise ValueError(f"DashScope API 调用失败: {e}")
    
    # 动态获取维度（从第一个 embedding 获取）
    if all_embeddings:
        dim = len(all_embeddings[0])
    else:
        dim = default_dim
    
    # 确保返回的向量数量与输入文本数量一致
    result = []
    for i, text in enumerate(texts):
        if i < len(all_embeddings):
            result.append(all_embeddings[i])
        else:
            result.append([0.0] * dim)
    
    return result
