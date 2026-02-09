"""
问答服务：支持基于知识库的 RAG（向量检索 + LLM）
"""
from typing import Optional, AsyncGenerator, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime

from app.models.conversation import Conversation, Message
from app.models.chunk import Chunk
from app.schemas.chat import ChatResponse, ConversationResponse, ConversationListResponse
from app.services.embedding_service import get_embedding
from app.services.llm_service import chat_completion as llm_chat
from app.services.vector_store import get_vector_client, chunk_id_to_vector_id
from app.services.rerank_service import rerank
from app.core.config import settings
from sqlalchemy.orm import selectinload
from sqlalchemy import or_


class ChatService:
    """问答服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def _rrf_score(self, rank: int, k: int = 60) -> float:
        """计算 RRF（Reciprocal Rank Fusion）分数。
        
        Args:
            rank: 文档在排名列表中的位置（从 1 开始）
            k: RRF 常数（默认 60）
        
        Returns:
            RRF 分数
        """
        return 1.0 / (k + rank)
    
    async def _full_text_search(self, query: str, knowledge_base_id: int, top_k: int = 50) -> List[tuple]:
        """全文匹配搜索：使用 SQL LIKE 进行关键词匹配。
        
        Args:
            query: 查询文本
            knowledge_base_id: 知识库ID
            top_k: 返回前 k 个结果
        
        Returns:
            List[tuple[Chunk, int]]: (chunk, rank) 列表，rank 从 1 开始
        """
        # 提取查询关键词（简单分词，去除常见停用词）
        import re
        keywords = [w.strip() for w in re.split(r'[，。！？\s]+', query) if len(w.strip()) > 1]
        if not keywords:
            keywords = [query]
        
        # 构建 LIKE 查询条件
        conditions = []
        for keyword in keywords[:5]:  # 最多使用前5个关键词
            conditions.append(Chunk.content.like(f"%{keyword}%"))
        
        if not conditions:
            return []
        
        # 执行查询
        result = await self.db.execute(
            select(Chunk)
            .where(
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.content != "",
                or_(*conditions)
            )
            .limit(top_k * 2)  # 多取一些，后续会 rerank
        )
        chunks = result.scalars().all()
        
        # 计算匹配分数（匹配的关键词数量）
        chunk_scores = []
        for chunk in chunks:
            score = 0
            for keyword in keywords:
                if keyword.lower() in chunk.content.lower():
                    score += 1
            if score > 0:
                chunk_scores.append((chunk, score))
        
        # 按匹配分数排序
        chunk_scores.sort(key=lambda x: x[1], reverse=True)
        
        # 返回前 top_k 个，并添加排名（从1开始）
        return [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[:top_k])]
    
    async def _rag_context(self, message: str, knowledge_base_id: int, top_k: int = 10) -> tuple[str, float, Optional[str]]:
        """根据用户问题在知识库中检索最相关上下文；使用向量检索+全文匹配+RRF+rerank。
        
        流程：
        1. 向量检索：获取向量相似度结果
        2. 全文匹配：使用 SQL LIKE 进行关键词匹配
        3. RRF 混合打分：合并两种检索结果的排名
        4. Rerank 重排序：使用 rerank 模型对候选结果重排序
        
        Returns:
            (context: str, confidence: float, max_confidence_context: Optional[str]): 
            上下文内容、最高置信度（0-1，1表示完全相似）、最高置信度对应的单个上下文
        """
        import logging
        
        # 1. 向量检索
        vector_results = []  # List[tuple[Chunk, rank, confidence]]
        vector_chunk_map = {}  # chunk_id -> Chunk
        try:
            query_vec = await get_embedding(message)
            vs = get_vector_client()
            hits = vs.search(query_vector=query_vec, top_k=top_k * 3, filter_expr=None) or []
            
            # 提取 vector_ids 和置信度
            vector_ids = []
            vector_id_to_confidence = {}
            for rank, h in enumerate(hits if isinstance(hits, list) else [], 1):
                if not isinstance(h, dict):
                    continue
                distance = h.get("distance") or h.get("score")
                entity = h.get("entity") or h.get("payload") or h.get("data") or {}
                if distance is None and isinstance(entity, dict):
                    distance = entity.get("distance") or entity.get("score")
                if distance is None:
                    distance = 2.0
                
                confidence = max(0.0, min(1.0, 1.0 - distance)) if isinstance(distance, (int, float)) else 0.0
                vid = h.get("id") or (entity.get("id") if isinstance(entity, dict) else None)
                if vid is not None:
                    vid_str = str(vid)
                    vector_ids.append(vid_str)
                    vector_id_to_confidence[vid_str] = (rank, confidence)
            
            # 查询对应的 chunks
            if vector_ids:
                result = await self.db.execute(
                    select(Chunk).where(
                        Chunk.vector_id.in_(vector_ids),
                        Chunk.knowledge_base_id == knowledge_base_id,
                    )
                )
                chunks = result.scalars().all()
                vid_to_chunk = {c.vector_id: c for c in chunks}
                
                # 构建向量检索结果（按原始排名）
                for vid in vector_ids:
                    if vid in vid_to_chunk:
                        chunk = vid_to_chunk[vid]
                        rank, conf = vector_id_to_confidence[vid]
                        vector_results.append((chunk, rank, conf))
                        vector_chunk_map[chunk.id] = chunk
        except Exception as e:
            logging.warning(f"向量检索失败: {e}")
        
        # 2. 全文匹配
        fulltext_results = []  # List[tuple[Chunk, rank]]
        try:
            fulltext_results = await self._full_text_search(message, knowledge_base_id, top_k=top_k * 3)
            for chunk, rank in fulltext_results:
                if chunk.id not in vector_chunk_map:
                    vector_chunk_map[chunk.id] = chunk
        except Exception as e:
            logging.warning(f"全文匹配失败: {e}")
        
        # 如果没有检索到任何结果，走兜底逻辑
        if not vector_results and not fulltext_results:
            result = await self.db.execute(
                select(Chunk).where(
                    Chunk.knowledge_base_id == knowledge_base_id,
                    Chunk.content != "",
                ).order_by(Chunk.id).limit(top_k * 2)
            )
            all_chunks = result.scalars().all()
            if all_chunks:
                context = "\n\n".join(c.content for c in all_chunks if c.content)[:8000]
                max_conf_context = all_chunks[0].content if all_chunks else None
                return (context, 0.5, max_conf_context)
            return ("", 0.0, None)
        
        # 3. RRF 混合打分
        chunk_rrf_scores = {}  # chunk_id -> RRF_score
        k = settings.RRF_K
        
        # 向量检索结果的 RRF 分数
        for chunk, rank, conf in vector_results:
            rrf_score = self._rrf_score(rank, k)
            chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + rrf_score
        
        # 全文匹配结果的 RRF 分数
        for chunk, rank in fulltext_results:
            rrf_score = self._rrf_score(rank, k)
            chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + rrf_score
        
        # 按 RRF 分数排序，取前 top_k * 2 作为 rerank 候选
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True
        )[:top_k * 2]
        
        if not candidate_chunks:
            return ("", 0.0, None)
        
        # 4. Rerank 重排序
        try:
            documents = [chunk.content for chunk, _ in candidate_chunks]
            reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
            
            # 构建最终结果
            final_chunks = []
            for item in reranked:
                idx = item["index"]
                if idx < len(candidate_chunks):
                    chunk, rrf_score = candidate_chunks[idx]
                    relevance_score = item.get("relevance_score", 0.0)
                    final_chunks.append((chunk, relevance_score, rrf_score))
            
            # 如果没有 rerank 结果，使用 RRF 排序的结果
            if not final_chunks:
                final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        except Exception as e:
            logging.warning(f"Rerank 失败: {e}，使用 RRF 排序结果")
            # Rerank 失败时，使用 RRF 排序的结果
            final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        
        # 取前 top_k 个结果
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None)
        
        # 构建上下文
        context = "\n\n".join(c.content for c, _, _ in selected_chunks if c.content)[:8000]
        
        # 最高置信度（使用 rerank 的 relevance_score，如果没有则使用 RRF 分数归一化）
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            # 如果没有 rerank 分数，使用 RRF 分数归一化
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)  # 粗略归一化到 0-1
        
        # 最高置信度对应的单个上下文
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        
        return (context, max_conf, max_conf_context)

    async def _rag_context_all_kbs(self, message: str, user_id: int, top_k: int = 10) -> tuple[str, float, Optional[str]]:
        """在所有知识库中检索最相关上下文；使用向量检索+全文匹配+RRF+rerank。
        
        Returns:
            (context: str, confidence: float, max_confidence_context: Optional[str]): 
            上下文内容、最高置信度（0-1）、最高置信度对应的单个上下文
        """
        import logging
        from app.models.knowledge_base import KnowledgeBase
        
        # 获取用户的所有知识库 ID
        kb_result = await self.db.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id)
        )
        kb_ids = [kb_id for kb_id in kb_result.scalars().all()]
        if not kb_ids:
            return ("", 0.0, None)
        
        # 1. 向量检索
        vector_results = []  # List[tuple[Chunk, rank, confidence]]
        vector_chunk_map = {}  # chunk_id -> Chunk
        try:
            query_vec = await get_embedding(message)
            vs = get_vector_client()
            hits = vs.search(query_vector=query_vec, top_k=top_k * 3, filter_expr=None) or []
            
            # 提取 vector_ids 和置信度
            vector_ids = []
            vector_id_to_confidence = {}
            for rank, h in enumerate(hits if isinstance(hits, list) else [], 1):
                if not isinstance(h, dict):
                    continue
                distance = h.get("distance") or h.get("score")
                entity = h.get("entity") or h.get("payload") or h.get("data") or {}
                if distance is None and isinstance(entity, dict):
                    distance = entity.get("distance") or entity.get("score")
                if distance is None:
                    distance = 2.0
                
                confidence = max(0.0, min(1.0, 1.0 - distance)) if isinstance(distance, (int, float)) else 0.0
                vid = h.get("id") or (entity.get("id") if isinstance(entity, dict) else None)
                if vid is not None:
                    vid_str = str(vid)
                    vector_ids.append(vid_str)
                    vector_id_to_confidence[vid_str] = (rank, confidence)
            
            # 查询对应的 chunks（过滤属于用户知识库的）
            if vector_ids:
                result = await self.db.execute(
                    select(Chunk).where(
                        Chunk.vector_id.in_(vector_ids),
                        Chunk.knowledge_base_id.in_(kb_ids),
                    )
                )
                chunks = result.scalars().all()
                vid_to_chunk = {c.vector_id: c for c in chunks}
                
                # 构建向量检索结果（按原始排名）
                for vid in vector_ids:
                    if vid in vid_to_chunk:
                        chunk = vid_to_chunk[vid]
                        rank, conf = vector_id_to_confidence[vid]
                        vector_results.append((chunk, rank, conf))
                        vector_chunk_map[chunk.id] = chunk
        except Exception as e:
            logging.warning(f"向量检索失败: {e}")
        
        # 2. 全文匹配（在所有知识库中搜索）
        fulltext_results = []  # List[tuple[Chunk, rank]]
        try:
            import re
            keywords = [w.strip() for w in re.split(r'[，。！？\s]+', message) if len(w.strip()) > 1]
            if not keywords:
                keywords = [message]
            
            conditions = []
            for keyword in keywords[:5]:
                conditions.append(Chunk.content.like(f"%{keyword}%"))
            
            if conditions:
                result = await self.db.execute(
                    select(Chunk)
                    .where(
                        Chunk.knowledge_base_id.in_(kb_ids),
                        Chunk.content != "",
                        or_(*conditions)
                    )
                    .limit(top_k * 3)
                )
                chunks = result.scalars().all()
                
                chunk_scores = []
                for chunk in chunks:
                    score = sum(1 for keyword in keywords if keyword.lower() in chunk.content.lower())
                    if score > 0:
                        chunk_scores.append((chunk, score))
                
                chunk_scores.sort(key=lambda x: x[1], reverse=True)
                fulltext_results = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[:top_k * 3])]
                
                for chunk, rank in fulltext_results:
                    if chunk.id not in vector_chunk_map:
                        vector_chunk_map[chunk.id] = chunk
        except Exception as e:
            logging.warning(f"全文匹配失败: {e}")
        
        # 如果没有检索到任何结果
        if not vector_results and not fulltext_results:
            return ("", 0.0, None)
        
        # 3. RRF 混合打分
        chunk_rrf_scores = {}
        k = settings.RRF_K
        
        for chunk, rank, conf in vector_results:
            rrf_score = self._rrf_score(rank, k)
            chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + rrf_score
        
        for chunk, rank in fulltext_results:
            rrf_score = self._rrf_score(rank, k)
            chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + rrf_score
        
        # 按 RRF 分数排序，取前 top_k * 2 作为 rerank 候选
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True
        )[:top_k * 2]
        
        if not candidate_chunks:
            return ("", 0.0, None)
        
        # 4. Rerank 重排序
        try:
            documents = [chunk.content for chunk, _ in candidate_chunks]
            reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
            
            final_chunks = []
            for item in reranked:
                idx = item["index"]
                if idx < len(candidate_chunks):
                    chunk, rrf_score = candidate_chunks[idx]
                    relevance_score = item.get("relevance_score", 0.0)
                    final_chunks.append((chunk, relevance_score, rrf_score))
            
            if not final_chunks:
                final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        except Exception as e:
            logging.warning(f"Rerank 失败: {e}，使用 RRF 排序结果")
            final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        
        # 取前 top_k 个结果
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None)
        
        context = "\n\n".join(c.content for c, _, _ in selected_chunks if c.content)[:8000]
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        
        return (context, max_conf, max_conf_context)

    async def _load_conversation_history(self, conversation_id: int, max_messages: int = None) -> List[Message]:
        """加载对话历史消息（最近 N 条）"""
        if max_messages is None:
            max_messages = settings.CHAT_CONTEXT_MESSAGE_COUNT
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(max_messages * 2)  # 多取一些，用于总结
        )
        messages = list(result.scalars().all())
        messages.reverse()  # 按时间正序
        return messages

    async def _summarize_old_messages(self, messages: List[Message]) -> str:
        """用 LLM 总结旧消息（超过上下文条数时）"""
        if len(messages) <= settings.CHAT_CONTEXT_MESSAGE_COUNT:
            return ""
        # 取前 N 条用于总结，保留后 M 条（M = CONTEXT_MESSAGE_COUNT）
        to_summarize = messages[:-settings.CHAT_CONTEXT_MESSAGE_COUNT]
        summary_prompt = "请用简洁的语言总结以下对话历史，保留关键信息：\n\n"
        summary_prompt += "\n".join(
            f"{'用户' if m.role == 'user' else '助手'}: {m.content[:200]}"
            for m in to_summarize
        )
        try:
            summary = await llm_chat(
                user_content=summary_prompt,
                system_content="你是一个对话总结助手，请用简洁的语言总结对话历史，保留关键信息。",
                context="",
            )
            return summary[:500]
        except Exception:
            return ""

    async def _build_chat_history_context(self, conversation_id: int) -> str:
        """构建对话历史上下文（最近 N 条，超过则总结）"""
        messages = await self._load_conversation_history(conversation_id)
        if not messages:
            return ""
        summary = ""
        if len(messages) > settings.CHAT_CONTEXT_MESSAGE_COUNT:
            summary = await self._summarize_old_messages(messages)
            messages = messages[-settings.CHAT_CONTEXT_MESSAGE_COUNT:]
        history_lines = []
        if summary:
            history_lines.append(f"[对话历史总结] {summary}")
        for m in messages:
            role_name = "用户" if m.role == "user" else "助手"
            history_lines.append(f"{role_name}: {m.content}")
        return "\n\n".join(history_lines)

    async def chat(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        stream: bool = False
    ) -> ChatResponse:
        """发送消息：可选基于知识库 RAG（向量检索 + LLM）+ 对话历史"""
        # 获取或创建对话
        if conversation_id:
            conv = await self.get_conversation(conversation_id, user_id)
            if not conv:
                raise ValueError("对话不存在")
        else:
            conv = Conversation(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                title=message[:50] if len(message) > 50 else message
            )
            self.db.add(conv)
            await self.db.commit()
            await self.db.refresh(conv)
        
        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=message
        )
        self.db.add(user_msg)
        await self.db.flush()
        
        # RAG 上下文（知识库检索）
        rag_context = ""
        rag_confidence = 0.0
        low_confidence_warning = ""
        retrieved_context_original = ""  # 保存原始检索上下文（不含警告）
        
        max_confidence_context = None  # 最高置信度对应的单个上下文
        if knowledge_base_id:
            # 指定了知识库，只在该知识库中检索
            rag_context, rag_confidence, max_confidence_context = await self._rag_context(message, knowledge_base_id, top_k=10)
            retrieved_context_original = rag_context  # 保存原始上下文
            if not rag_context.strip():
                try:
                    fallback = await self.db.execute(
                        select(Chunk).where(
                            Chunk.knowledge_base_id == knowledge_base_id,
                            Chunk.content != "",
                        ).order_by(Chunk.id).limit(20)
                    )
                    chunks = fallback.scalars().all()
                    if chunks:
                        rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                        retrieved_context_original = rag_context
                        rag_confidence = 0.5  # 兜底时给中等置信度
                except Exception:
                    pass
            if not rag_context.strip():
                rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
        else:
            # 未指定知识库，在所有知识库中检索
            rag_context, rag_confidence, max_confidence_context = await self._rag_context_all_kbs(message, user_id, top_k=10)
            retrieved_context_original = rag_context  # 保存原始上下文
            if rag_context and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
                # 置信度低于阈值，提示用户并使用 LLM 自身知识
                low_confidence_warning = f"[系统提示：当前内部知识库检索结果的置信度为 {rag_confidence:.2f}，低于阈值 {settings.RAG_CONFIDENCE_THRESHOLD}。请明确告知用户「当前内部知识库置信度比较低，将使用AI自身知识解答问题」，然后结合检索到的上下文（如有）和AI自身知识回答问题。]"
                # 仍然使用检索到的上下文，但添加警告
                rag_context = low_confidence_warning + "\n\n" + rag_context if rag_context else low_confidence_warning

        # 对话历史上下文
        history_context = await self._build_chat_history_context(conv.id)
        
        # 合并上下文
        full_context = ""
        if rag_context:
            if low_confidence_warning and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
                # 低置信度时，明确告知 LLM 使用自身知识
                full_context += f"【知识库上下文（置信度较低，请结合AI自身知识）】\n{rag_context}\n\n"
            else:
                full_context += f"【知识库上下文】\n{rag_context}\n\n"
        if history_context:
            full_context += f"【对话历史】\n{history_context}\n\n"

        try:
            assistant_content = await llm_chat(
                user_content=message,
                context=full_context.strip(),
            )
        except Exception:
            assistant_content = "抱歉，当前无法生成回答，请检查模型配置或网络。"
        
        # 判断是否有真实的检索结果
        has_real_retrieval = (
            (retrieved_context_original and 
             retrieved_context_original.strip() and 
             not retrieved_context_original.startswith("[系统提示：")) or
            (max_confidence_context and max_confidence_context.strip())
        )
        
        assistant_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=assistant_content,
            tokens=len(assistant_content) // 2,
            model=settings.LLM_MODEL,
            confidence=str(rag_confidence) if has_real_retrieval else None,  # 存储为字符串
            retrieved_context=retrieved_context_original if (has_real_retrieval and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD) else None,
            max_confidence_context=max_confidence_context if max_confidence_context else None,
        )
        self.db.add(assistant_msg)
        # 更新对话标题（第一条消息时）和更新时间（模型有 onupdate，但显式更新更可靠）
        if not conv.title or conv.title == message[:50]:
            conv.title = message[:50] if len(message) > 50 else message
        await self.db.commit()
        await self.db.refresh(conv)  # 刷新以获取 updated_at
        
        # 返回置信度和检索上下文
        # 判断是否有真实的检索结果：
        # 1. retrieved_context_original 不为空且不是系统提示
        # 2. 或者 max_confidence_context 不为空（说明有检索到内容）
        has_real_retrieval = (
            (retrieved_context_original and 
             retrieved_context_original.strip() and 
             not retrieved_context_original.startswith("[系统提示：")) or
            (max_confidence_context and max_confidence_context.strip())
        )
        
        # 如果有真实检索结果，总是返回置信度（即使为 0 或很低）
        return_confidence = rag_confidence if has_real_retrieval else None
        
        # 返回所有检索上下文（仅在低置信度时，用于显示）
        return_context = None
        if has_real_retrieval and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
            # 优先使用 retrieved_context_original，如果没有则使用 rag_context（去除系统提示）
            if retrieved_context_original and retrieved_context_original.strip() and not retrieved_context_original.startswith("[系统提示："):
                return_context = retrieved_context_original
            elif rag_context and rag_context.strip() and not rag_context.startswith("[系统提示："):
                return_context = rag_context
        
        return ChatResponse(
            conversation_id=conv.id,
            message=assistant_content,
            tokens=assistant_msg.tokens,
            model=assistant_msg.model,
            created_at=datetime.utcnow(),
            confidence=return_confidence,
            retrieved_context=return_context,
            max_confidence_context=max_confidence_context  # 总是返回最高置信度对应的单个上下文（如果有）
        )
    
    async def chat_stream(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """流式发送消息"""
        # TODO: 实现流式响应
        response = await self.chat(user_id, message, conversation_id, knowledge_base_id)
        yield response.message
    
    async def get_conversations(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = None
    ) -> ConversationListResponse:
        """获取对话列表（限制最多保存数量）"""
        if page_size is None:
            page_size = settings.CHAT_HISTORY_DEFAULT_COUNT
        page_size = min(page_size, settings.CHAT_HISTORY_MAX_COUNT)
        offset = (page - 1) * page_size
        
        count_result = await self.db.execute(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
        )
        total = count_result.scalar()
        
        # 限制总数不超过配置的最大值
        if total > settings.CHAT_HISTORY_MAX_COUNT:
            # 删除最旧的对话
            oldest_result = await self.db.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.asc())
                .limit(total - settings.CHAT_HISTORY_MAX_COUNT)
            )
            oldest_convs = oldest_result.scalars().all()
            for conv in oldest_convs:
                self.db.delete(conv)
            await self.db.commit()
            total = settings.CHAT_HISTORY_MAX_COUNT
        
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        conversations = result.scalars().all()
        
        # 序列化时显式设置 messages=[]，避免触发懒加载
        conv_responses = []
        for conv in conversations:
            conv_responses.append(ConversationResponse(
                id=conv.id,
                title=conv.title,
                knowledge_base_id=conv.knowledge_base_id,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                messages=[]  # 列表不需要消息详情
            ))
        
        return ConversationListResponse(
            conversations=conv_responses,
            total=total,
            page=page,
            page_size=page_size
        )
    
    async def get_conversation(self, conv_id: int, user_id: int) -> Optional[Conversation]:
        """获取对话（含消息列表）"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id == conv_id, Conversation.user_id == user_id)
            .options(selectinload(Conversation.messages))
        )
        return result.scalar_one_or_none()
    
    async def get_conversation_messages(
        self, conv_id: int, user_id: int, limit: int = 100
    ) -> List[Message]:
        """获取对话的消息列表"""
        # 先校验对话归属
        conv = await self.get_conversation(conv_id, user_id)
        if not conv:
            return []
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def delete_conversation(self, conv_id: int, user_id: int) -> None:
        """删除对话"""
        conv = await self.get_conversation(conv_id, user_id)
        if not conv:
            raise ValueError("对话不存在")
        
        await self.db.delete(conv)
        await self.db.commit()
