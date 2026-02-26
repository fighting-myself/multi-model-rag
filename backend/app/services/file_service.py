"""
文件服务
"""
import hashlib
import os
from typing import List, Optional
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.models.file import File, FileStatus
from app.models.chunk import Chunk
from app.models.knowledge_base import KnowledgeBaseFile
from app.schemas.file import FileResponse, FileListResponse
from app.services.vector_store import get_vector_client
from app.services.file_security_service import (
    validate_filename,
    validate_file_content,
    virus_scan_content,
)


class FileService:
    """文件服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        # 确保bucket存在
        try:
            if not self.minio_client.bucket_exists(settings.MINIO_BUCKET_NAME):
                self.minio_client.make_bucket(settings.MINIO_BUCKET_NAME)
        except S3Error:
            pass
    
    def _calculate_md5(self, content: bytes) -> str:
        """计算MD5"""
        return hashlib.md5(content).hexdigest()
    
    def _get_file_type(self, filename: str) -> str:
        """获取文件类型"""
        ext = filename.split('.')[-1].lower()
        return ext
    
    async def upload_file(
        self,
        file: UploadFile,
        user_id: int,
        knowledge_base_id: Optional[int] = None,
        on_duplicate: Optional[str] = None,
    ) -> File:
        """上传文件。on_duplicate: use_existing=同 MD5 返回已有；overwrite=覆盖已有（同用户同 MD5）并清空分块。"""
        content = await file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise ValueError(f"文件大小超过限制（{settings.MAX_FILE_SIZE}字节）")
        validate_filename(file.filename or "")
        file_type = self._get_file_type(file.filename)
        allowed = settings.allowed_file_types_list
        if file_type not in allowed:
            raise ValueError(
                f"不支持的文件类型: {file_type}。当前允许: {', '.join(allowed)}。"
                "可在 .env 中设置 ALLOWED_FILE_TYPES 增加类型。"
            )
        validate_file_content(content, file_type)
        ok, scan_msg = virus_scan_content(content)
        if not ok:
            raise ValueError(f"文件未通过安全扫描: {scan_msg or '检测到恶意内容'}")
        md5_hash = self._calculate_md5(content)
        policy = (on_duplicate or settings.UPLOAD_ON_DUPLICATE or "use_existing").strip().lower()
        if policy not in ("use_existing", "overwrite"):
            policy = "use_existing"

        existing_result = await self.db.execute(
            select(File).where(File.md5_hash == md5_hash, File.user_id == user_id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            if policy == "overwrite":
                await self._overwrite_file(existing, content, file, file_type)
                return existing
            return existing

        storage_path = f"{user_id}/{md5_hash}/{file.filename}"
        try:
            from io import BytesIO
            file_obj = BytesIO(content)
            file_obj.seek(0)
            self.minio_client.put_object(
                settings.MINIO_BUCKET_NAME,
                storage_path,
                file_obj,
                length=len(content),
                content_type=file.content_type or "application/octet-stream"
            )
        except Exception as e:
            raise ValueError(f"文件上传失败: {str(e)}")
        file_record = File(
            user_id=user_id,
            filename=file.filename,
            original_filename=file.filename,
            file_type=file_type,
            file_size=len(content),
            storage_path=storage_path,
            md5_hash=md5_hash,
            status=FileStatus.COMPLETED
        )
        self.db.add(file_record)
        await self.db.commit()
        await self.db.refresh(file_record)
        return file_record

    async def _overwrite_file(self, existing: File, content: bytes, file: UploadFile, file_type: str) -> None:
        """覆盖已有文件：删该文件的 chunk 与向量、知识库关联，覆盖 MinIO，更新记录。"""
        chunk_result = await self.db.execute(select(Chunk.id).where(Chunk.file_id == existing.id))
        chunk_ids = [r for r in chunk_result.scalars().all()]
        await self.db.execute(delete(Chunk).where(Chunk.file_id == existing.id))
        await self.db.execute(delete(KnowledgeBaseFile).where(KnowledgeBaseFile.file_id == existing.id))
        if chunk_ids:
            try:
                vs = get_vector_client()
                vs.delete_by_chunk_ids(chunk_ids)
            except Exception:
                pass
        try:
            from io import BytesIO
            self.minio_client.put_object(
                settings.MINIO_BUCKET_NAME,
                existing.storage_path,
                BytesIO(content),
                length=len(content),
                content_type=file.content_type or "application/octet-stream"
            )
        except Exception:
            pass
        existing.file_size = len(content)
        existing.chunk_count = 0
        existing.original_filename = file.filename
        existing.filename = file.filename
        existing.file_type = file_type
        existing.status = FileStatus.COMPLETED
        await self.db.commit()
        await self.db.refresh(existing)
    
    async def batch_upload_files(
        self,
        files: List[UploadFile],
        user_id: int,
        knowledge_base_id: Optional[int] = None,
        on_duplicate: Optional[str] = None,
    ) -> List[File]:
        """批量上传文件"""
        results = []
        for file in files:
            try:
                file_record = await self.upload_file(
                    file, user_id, knowledge_base_id, on_duplicate=on_duplicate
                )
                results.append(file_record)
            except Exception as e:
                print(f"文件 {file.filename} 上传失败: {str(e)}")
        return results
    
    async def get_files(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20
    ) -> FileListResponse:
        """获取文件列表"""
        offset = (page - 1) * page_size
        
        # 查询总数
        count_result = await self.db.execute(
            select(func.count()).select_from(File).where(File.user_id == user_id)
        )
        total = count_result.scalar()
        
        # 查询文件列表
        result = await self.db.execute(
            select(File)
            .where(File.user_id == user_id)
            .order_by(File.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        files = result.scalars().all()
        
        return FileListResponse(
            files=[FileResponse.model_validate(f) for f in files],
            total=total,
            page=page,
            page_size=page_size
        )
    
    async def get_file(self, file_id: int, user_id: int) -> Optional[File]:
        """获取文件"""
        result = await self.db.execute(
            select(File).where(File.id == file_id, File.user_id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def delete_file(self, file_id: int, user_id: int) -> None:
        """删除文件（先删关联的 Chunk、KnowledgeBaseFile，再删文件）"""
        file = await self.get_file(file_id, user_id)
        if not file:
            raise ValueError("文件不存在")

        # 先删关联的 chunks（file_id NOT NULL，不能只置空）
        await self.db.execute(delete(Chunk).where(Chunk.file_id == file_id))
        # 再删知识库-文件关联
        await self.db.execute(delete(KnowledgeBaseFile).where(KnowledgeBaseFile.file_id == file_id))

        # 从 MinIO 删除对象
        try:
            self.minio_client.remove_object(settings.MINIO_BUCKET_NAME, file.storage_path)
        except Exception:
            pass

        # 最后删除文件记录
        await self.db.delete(file)
        await self.db.commit()
    
    async def download_file(self, file_id: int, user_id: int) -> Optional[dict]:
        """下载文件：读入完整字节并返回，便于前端展示/下载"""
        file = await self.get_file(file_id, user_id)
        if not file:
            return None
        try:
            response = self.minio_client.get_object(settings.MINIO_BUCKET_NAME, file.storage_path)
            data = response.read()
            response.close()
            ft = (file.file_type or "").lower()
            if ft in ("jpeg", "jpg", "png", "gif", "webp"):
                content_type = f"image/{ft}" if ft != "jpg" else "image/jpeg"
            else:
                content_type = f"application/{file.file_type}"
            return {
                "content": data,
                "filename": file.original_filename,
                "content_type": content_type,
            }
        except Exception:
            return None

    async def get_file_content(
        self, file_id: int, user_id: int
    ) -> tuple[Optional[bytes], Optional[str]]:
        """获取文件原始字节。返回 (content, error_reason)：成功时 error_reason 为 None，失败时 content 为 None 且 error_reason 为可展示原因。"""
        import logging
        file = await self.get_file(file_id, user_id)
        if not file:
            logging.warning(f"get_file_content: 文件 {file_id} 不存在或无权访问 (user_id={user_id})")
            return None, "文件不存在或无权访问"
        try:
            response = self.minio_client.get_object(settings.MINIO_BUCKET_NAME, file.storage_path)
            data = response.read()
            response.close()
            if not data or len(data) == 0:
                logging.warning(f"get_file_content: 文件 {file_id} MinIO 对象为空 (path={file.storage_path})")
                return None, "对象存储中文件为空"
            return data, None
        except Exception as e:
            err_str = str(e).lower()
            logging.warning(f"get_file_content: 文件 {file_id} 读取失败 path={file.storage_path} err={e}")
            if "nosuchkey" in err_str or "not found" in err_str or "does not exist" in err_str:
                return None, "对象存储中不存在该文件，请重新上传后再添加到知识库"
            return None, f"读取失败: {e}"
