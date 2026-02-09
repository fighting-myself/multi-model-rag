"""
Rerank 服务：使用阿里云百炼 DashScope rerank API 对检索结果重排序
"""
import httpx
from typing import List, Dict, Any
from app.core.config import settings


async def rerank(query: str, documents: List[str], top_n: int = 5) -> List[Dict[str, Any]]:
    """使用 DashScope rerank API 对文档进行重排序。
    
    Args:
        query: 查询文本
        documents: 文档列表
        top_n: 返回前 N 个结果
    
    Returns:
        List[Dict]: 排序后的结果列表，每个元素包含：
            - index: 原始索引
            - document: 文档内容
            - relevance_score: 相关性分数（0-1）
    """
    if not documents:
        return []
    
    import logging
    
    # DashScope rerank API
    url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    headers = {
        "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.RERANK_MODEL,
        "input": {
            "query": query,
            "documents": documents
        },
        "parameters": {
            "return_documents": True,
            "top_n": min(top_n, len(documents))
        }
    }
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            
            # 解析响应
            reranked_results = []
            if "output" in result and "results" in result["output"]:
                for item in result["output"]["results"]:
                    reranked_results.append({
                        "index": item.get("index", 0),
                        "document": item.get("document", ""),
                        "relevance_score": item.get("relevance_score", 0.0)
                    })
            else:
                logging.error(f"DashScope rerank API 响应格式错误: {result}")
                # 如果响应格式错误，返回原始顺序
                for idx, doc in enumerate(documents):
                    reranked_results.append({
                        "index": idx,
                        "document": doc,
                        "relevance_score": 0.5
                    })
            
            return reranked_results
            
    except httpx.HTTPStatusError as e:
        logging.error(f"DashScope rerank API HTTP 错误: {e.response.status_code} - {e.response.text}")
        # 失败时返回原始顺序
        return [{"index": idx, "document": doc, "relevance_score": 0.5} for idx, doc in enumerate(documents)]
    except Exception as e:
        logging.error(f"DashScope rerank API 调用异常: {e}")
        # 失败时返回原始顺序
        return [{"index": idx, "document": doc, "relevance_score": 0.5} for idx, doc in enumerate(documents)]
