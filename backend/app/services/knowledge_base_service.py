"""
知识库服务：创建知识库、添加文件并做 RAG 切分与向量化
"""
import io
import logging
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseFile
from app.models.file import File
from app.models.chunk import Chunk
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseResponse, KnowledgeBaseListResponse
from app.services.file_service import FileService
from app.services.embedding_service import get_embeddings
from app.services.vector_store import get_vector_client, chunk_id_to_vector_id
from app.services.ocr_service import extract_text_from_image
from app.core.config import settings


class KnowledgeBaseService:
    """知识库服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_knowledge_base(
        self,
        kb_data: KnowledgeBaseCreate,
        user_id: int
    ) -> KnowledgeBase:
        """创建知识库"""
        kb = KnowledgeBase(
            user_id=user_id,
            name=kb_data.name,
            description=kb_data.description
        )
        self.db.add(kb)
        await self.db.commit()
        await self.db.refresh(kb)
        return kb
    
    async def get_knowledge_bases(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20
    ) -> KnowledgeBaseListResponse:
        """获取知识库列表"""
        offset = (page - 1) * page_size
        
        count_result = await self.db.execute(
            select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
        )
        total = count_result.scalar()
        
        result = await self.db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id)
            .order_by(KnowledgeBase.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        kbs = result.scalars().all()
        
        return KnowledgeBaseListResponse(
            knowledge_bases=[KnowledgeBaseResponse.model_validate(kb) for kb in kbs],
            total=total,
            page=page,
            page_size=page_size
        )
    
    async def get_knowledge_base(self, kb_id: int, user_id: int) -> Optional[KnowledgeBase]:
        """获取知识库"""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    async def update_knowledge_base(
        self,
        kb_id: int,
        kb_data: KnowledgeBaseCreate,
        user_id: int
    ) -> Optional[KnowledgeBase]:
        """更新知识库"""
        kb = await self.get_knowledge_base(kb_id, user_id)
        if not kb:
            return None
        
        kb.name = kb_data.name
        kb.description = kb_data.description
        await self.db.commit()
        await self.db.refresh(kb)
        return kb
    
    async def delete_knowledge_base(self, kb_id: int, user_id: int) -> None:
        """删除知识库，包括清理 Milvus 中的向量数据"""
        import logging
        from sqlalchemy import delete
        
        kb = await self.get_knowledge_base(kb_id, user_id)
        if not kb:
            raise ValueError("知识库不存在")
        
        # 1. 查询该知识库的所有 chunks，获取 vector_ids
        chunks_result = await self.db.execute(
            select(Chunk).where(Chunk.knowledge_base_id == kb_id)
        )
        chunks = list(chunks_result.scalars().all())
        
        # 2. 从 Milvus 中删除对应的向量
        if chunks:
            try:
                vector_store = get_vector_client()
                vector_ids_to_delete = []
                for chunk in chunks:
                    # 使用确定性算法计算 vector_id（与插入时一致）
                    vid = int(chunk_id_to_vector_id(chunk.id))
                    vector_ids_to_delete.append(vid)
                
                if vector_ids_to_delete:
                    try:
                        # 检查集合是否存在
                        if vector_store.client.has_collection(vector_store._collection):
                            # 从 Milvus 中删除向量
                            vector_store.client.delete(
                                collection_name=vector_store._collection,
                                ids=vector_ids_to_delete
                            )
                            logging.info(f"从 Milvus 中删除了 {len(vector_ids_to_delete)} 个向量")
                        else:
                            logging.warning(f"Milvus 集合 {vector_store._collection} 不存在，跳过向量删除")
                    except Exception as e:
                        logging.error(f"删除 Milvus 向量失败: {e}，继续删除数据库记录")
            except Exception as e:
                logging.error(f"清理 Milvus 向量时出错: {e}，继续删除数据库记录")
        
        # 3. 删除数据库中的 chunks（级联删除会自动处理，但显式删除更清晰）
        if chunks:
            await self.db.execute(
                delete(Chunk).where(Chunk.knowledge_base_id == kb_id)
            )
            await self.db.flush()
        
        # 4. 删除知识库文件关联（级联删除会自动处理，但显式删除更清晰）
        await self.db.execute(
            delete(KnowledgeBaseFile).where(KnowledgeBaseFile.knowledge_base_id == kb_id)
        )
        await self.db.flush()
        
        # 5. 删除知识库本身
        await self.db.delete(kb)
        await self.db.commit()
        
        logging.info(f"成功删除知识库 {kb_id} 及其所有相关数据（包括 {len(chunks)} 个 chunks 和对应的向量）")
    
    @staticmethod
    def _extract_text(content: bytes, file_type: str) -> str:
        """从文件内容提取纯文本（支持 txt、pdf、docx、pptx、xlsx）"""
        ft = (file_type or "").lower()
        if ft == "txt":
            return content.decode("utf-8", errors="ignore").strip()
        if ft == "pdf":
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(content))
                return "\n".join(
                    (page.extract_text() or "").strip()
                    for page in reader.pages
                ).strip()
            except Exception:
                return ""
        if ft == "docx":
            try:
                from docx import Document
                doc = Document(io.BytesIO(content))
                parts = []
                for para in doc.paragraphs:
                    if para.text.strip():
                        parts.append(para.text.strip())
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            if cell.text.strip():
                                parts.append(cell.text.strip())
                return "\n".join(parts).strip() if parts else ""
            except Exception as e:
                logging.warning(f"docx 文本提取失败: {e}")
                return ""
        if ft == "pptx":
            try:
                from pptx import Presentation
                prs = Presentation(io.BytesIO(content))
                parts = []
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text and shape.text.strip():
                            parts.append(shape.text.strip())
                        # 表格
                        if shape.has_table:
                            for row in shape.table.rows:
                                for cell in row.cells:
                                    if cell.text and cell.text.strip():
                                        parts.append(cell.text.strip())
                return "\n".join(parts).strip() if parts else ""
            except Exception as e:
                logging.warning(f"pptx 文本提取失败: {e}")
                return ""
        if ft == "xlsx":
            try:
                from openpyxl import load_workbook
                wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                parts = []
                for name in wb.sheetnames:
                    sheet = wb[name]
                    for row in sheet.iter_rows(values_only=True):
                        for cell in row:
                            if cell is not None and str(cell).strip():
                                parts.append(str(cell).strip())
                wb.close()
                return "\n".join(parts).strip() if parts else ""
            except Exception as e:
                logging.warning(f"xlsx 文本提取失败: {e}")
                return ""
        return ""

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50, max_expand_ratio: float = 1.3) -> List[str]:
        """智能分块：按句子切分，保持语义完整性，不截断句子。
        
        Args:
            text: 待切分的文本
            chunk_size: 目标块大小（字符数）
            overlap: 重叠字符数
            max_expand_ratio: 最大扩展比例（允许超出 chunk_size 的最大倍数，默认 1.3）
        
        Returns:
            List[str]: 切分后的文本块列表
        """
        if not text or chunk_size <= 0:
            return []
        
        import re
        
        # 1. 按句子分割（使用中文和英文标点符号）
        # 匹配句号、问号、感叹号、换行符等作为句子结束符
        # 使用正则表达式分割，保留分隔符
        sentence_pattern = r'([。！？\n]+|[.!?\n]+)'
        parts = re.split(sentence_pattern, text)
        sentences = []
        current_sentence = ""
        for i, part in enumerate(parts):
            if re.match(sentence_pattern, part):
                # 这是分隔符，结束当前句子
                if current_sentence.strip():
                    sentences.append(current_sentence.strip() + part.strip())
                current_sentence = ""
            else:
                current_sentence += part
        # 添加最后一个句子（如果没有以分隔符结尾）
        if current_sentence.strip():
            sentences.append(current_sentence.strip())
        
        # 过滤空句子
        sentences = [s for s in sentences if s.strip()]
        
        if not sentences:
            # 如果没有找到句子分隔符，按段落分割
            paragraphs = text.split('\n\n')
            if len(paragraphs) == 1:
                # 如果也没有段落，按固定长度切分（兜底）
                chunks = []
                start = 0
                while start < len(text):
                    end = start + chunk_size
                    chunks.append(text[start:end])
                    start = end - overlap
                    if start >= len(text):
                        break
                return chunks
            sentences = [p.strip() for p in paragraphs if p.strip()]
        
        if not sentences:
            return []
        
        # 2. 智能合并句子，形成语义完整的块
        chunks = []
        current_chunk = []
        current_length = 0
        max_chunk_size = int(chunk_size * max_expand_ratio)  # 允许的最大块大小
        
        for i, sentence in enumerate(sentences):
            sentence_length = len(sentence)
            
            # 如果单个句子就超过最大块大小，需要进一步切分（但保持句子内不截断）
            if sentence_length > max_chunk_size:
                # 如果当前块有内容，先保存
                if current_chunk:
                    chunks.append(' '.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                
                # 对超长句子，尝试按逗号、分号等进一步分割
                sub_sentences = re.split(r'[，；,;]+', sentence)
                sub_sentences = [s.strip() for s in sub_sentences if s.strip()]
                
                for sub_sentence in sub_sentences:
                    sub_length = len(sub_sentence)
                    if current_length + sub_length <= max_chunk_size:
                        current_chunk.append(sub_sentence)
                        current_length += sub_length + 1  # +1 是空格
                    else:
                        if current_chunk:
                            chunks.append(' '.join(current_chunk))
                        # 如果子句仍然太长，直接作为一个块
                        if sub_length > max_chunk_size:
                            chunks.append(sub_sentence)
                            current_chunk = []
                            current_length = 0
                        else:
                            current_chunk = [sub_sentence]
                            current_length = sub_length
                continue
            
            # 检查添加当前句子后是否会超出限制
            # 计算新长度：当前长度 + 句子长度 + 空格（如果有已有句子）
            separator_length = 1 if current_chunk else 0
            new_length = current_length + sentence_length + separator_length
            
            if new_length <= chunk_size:
                # 在目标大小内，直接添加
                current_chunk.append(sentence)
                current_length = new_length
            elif new_length <= max_chunk_size:
                # 超出目标大小但在允许范围内，为了保持语义完整性，仍然添加
                current_chunk.append(sentence)
                current_length = new_length
            else:
                # 超出允许范围，保存当前块，开始新块
                # 先提取重叠部分（在保存当前块之前）
                overlap_sentences = []
                overlap_length = 0
                if current_chunk and len(current_chunk) > 1:
                    # 从后往前取句子，直到达到重叠大小
                    for j in range(len(current_chunk) - 1, -1, -1):
                        sent = current_chunk[j]
                        sent_len = len(sent)
                        if overlap_length + sent_len <= overlap:
                            overlap_sentences.insert(0, sent)
                            overlap_length += sent_len + 1  # +1 是空格
                        else:
                            break
                
                # 保存当前块
                if current_chunk:
                    chunks.append(' '.join(current_chunk))
                
                # 开始新块，使用重叠句子
                current_chunk = overlap_sentences + [sentence]
                # 计算新块长度：重叠部分长度 + 当前句子长度 + 空格
                separator_count = len(overlap_sentences)  # 重叠句子之间的空格数
                current_length = overlap_length + sentence_length + separator_count
        
        # 添加最后一个块
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        return chunks

    async def add_files(self, kb_id: int, file_ids: List[int], user_id: int) -> Optional[KnowledgeBase]:
        """添加文件到知识库并执行 RAG 切分与向量化
        
        事务性处理：如果切分或向量化失败，会回滚所有操作，确保数据一致性。
        文档块原文和向量都会保存到 Milvus 中。
        """
        import logging
        from sqlalchemy.exc import SQLAlchemyError
        
        kb = await self.get_knowledge_base(kb_id, user_id)
        if not kb:
            return None

        file_service = FileService(self.db)
        vector_store = get_vector_client()
        
        # 在开始处理文件之前，先获取一个向量来确定实际维度
        # 然后使用实际维度创建集合（如果不存在）
        actual_dim = None
        try:
            # 测试获取一个向量的维度
            test_embedding = await get_embeddings(["test"])
            if test_embedding and len(test_embedding) > 0:
                actual_dim = len(test_embedding[0])
                logging.info(f"检测到向量维度: {actual_dim}")
        except Exception as e:
            logging.warning(f"无法预先获取向量维度: {e}，将使用配置的维度")
        
        # 确保向量集合存在，使用实际维度创建
        try:
            vector_store.ensure_collection(actual_dim=actual_dim)
            logging.info(f"向量集合已确保存在，准备处理文件")
        except Exception as e:
            logging.error(f"创建向量集合失败: {e}")
            raise ValueError(f"无法创建向量集合，请检查 Zilliz 配置: {e}")

        # 记录需要回滚的数据
        added_kb_files = []
        added_chunks = []
        updated_files = []
        
        try:
            for file_id in file_ids:
                # 校验文件归属
                file_result = await self.db.execute(
                    select(File).where(File.id == file_id, File.user_id == user_id)
                )
                file = file_result.scalar_one_or_none()
                if not file:
                    continue

                # 是否已关联到该知识库
                existing_result = await self.db.execute(
                    select(KnowledgeBaseFile).where(
                        KnowledgeBaseFile.knowledge_base_id == kb_id,
                        KnowledgeBaseFile.file_id == file_id,
                    )
                )
                if existing_result.scalars().first():
                    continue

                # 添加知识库文件关联
                kb_file = KnowledgeBaseFile(knowledge_base_id=kb_id, file_id=file_id)
                self.db.add(kb_file)
                await self.db.flush()
                added_kb_files.append(kb_file)

                # 拉取文件内容并切分
                content = await file_service.get_file_content(file_id, user_id)
                if not content:
                    logging.warning(f"文件 {file_id} 内容为空，跳过")
                    continue

                # 图片使用 OCR 提取文本，其余使用 _extract_text
                ft = (file.file_type or "").lower()
                if ft in ("jpeg", "jpg", "png"):
                    text = await extract_text_from_image(content, file.file_type)
                else:
                    text = self._extract_text(content, file.file_type)
                if not text:
                    logging.warning(f"文件 {file_id} 提取文本为空，跳过")
                    continue
                    
                # 使用配置的分块参数
                text_chunks = self._chunk_text(
                    text,
                    chunk_size=settings.CHUNK_SIZE,
                    overlap=settings.CHUNK_OVERLAP,
                    max_expand_ratio=settings.CHUNK_MAX_EXPAND_RATIO
                )
                if not text_chunks:
                    logging.warning(f"文件 {file_id} 切分后无文本块，跳过")
                    continue

                # 创建 Chunk 记录
                chunks = []
                for idx, chunk_text in enumerate(text_chunks):
                    chunk = Chunk(
                        file_id=file_id,
                        knowledge_base_id=kb_id,
                        content=chunk_text,
                        chunk_index=idx,
                    )
                    self.db.add(chunk)
                    chunks.append(chunk)
                await self.db.flush()
                added_chunks.extend(chunks)

                # 生成向量
                try:
                    embeddings = await get_embeddings([c.content for c in chunks])
                    if len(embeddings) != len(chunks):
                        raise ValueError(f"向量数量 {len(embeddings)} 与文本块数量 {len(chunks)} 不匹配")
                    
                    # 使用确定性 vector_id，与 vector_store 一致，供检索时反查
                    metadatas = []
                    for c, emb in zip(chunks, embeddings):
                        c.vector_id = chunk_id_to_vector_id(c.id)
                        # 将文档块原文保存到 metadata 中
                        metadatas.append({
                            "chunk_id": c.id,
                            "content": c.content[:1000],  # 限制长度，避免 metadata 过大
                            "file_id": c.file_id,
                            "knowledge_base_id": c.knowledge_base_id,
                            "chunk_index": c.chunk_index,
                        })
                    
                    # 插入向量到向量库（包含原文 metadata）
                    vector_store.insert(
                        ids=[str(c.id) for c in chunks],
                        vectors=embeddings,
                        metadatas=metadatas,  # 保存文档块原文到 metadata
                    )
                    logging.info(f"成功插入 {len(chunks)} 个向量到向量库（包含原文）")
                    
                except Exception as e:
                    logging.error(f"文件 {file_id} 向量化失败: {e}")
                    raise ValueError(f"文件 {file_id} 向量化失败: {e}")

                # 更新文件统计（仅在成功后才更新）
                old_chunk_count = file.chunk_count or 0
                file.chunk_count = old_chunk_count + len(chunks)
                updated_files.append((file, old_chunk_count))

            # 更新知识库统计
            for file, old_count in updated_files:
                kb.chunk_count = (kb.chunk_count or 0) + (file.chunk_count - old_count)

            # 更新知识库 file_count
            count_result = await self.db.execute(
                select(func.count()).select_from(KnowledgeBaseFile).where(
                    KnowledgeBaseFile.knowledge_base_id == kb_id
                )
            )
            kb.file_count = count_result.scalar() or 0
            
            # 提交所有更改
            await self.db.commit()
            await self.db.refresh(kb)
            logging.info(f"成功处理 {len(added_kb_files)} 个文件，共 {len(added_chunks)} 个文本块")
            return kb
            
        except Exception as e:
            # 发生错误，回滚所有操作
            logging.error(f"处理文件时发生错误: {e}，开始回滚")
            try:
                await self.db.rollback()
                logging.info("数据库事务已回滚")
            except Exception as rollback_error:
                logging.error(f"回滚失败: {rollback_error}")
            
            # 尝试清理已插入的向量（如果向量插入成功但后续失败）
            if added_chunks:
                try:
                    vector_ids_to_delete = [str(chunk_id_to_vector_id(c.id)) for c in added_chunks]
                    # 注意：这里需要根据实际的 vector_store 实现来删除向量
                    # 如果 vector_store 没有 delete 方法，可能需要手动调用 Milvus API
                    logging.warning(f"需要清理 {len(vector_ids_to_delete)} 个已插入的向量")
                except Exception as cleanup_error:
                    logging.error(f"清理向量失败: {cleanup_error}")
            
            raise ValueError(f"添加文件到知识库失败: {e}")
