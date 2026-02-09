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
        knowledge_base_id: Optional[int] = None
    ) -> File:
        """上传文件"""
        # 读取文件内容
        content = await file.read()
        
        # 验证文件大小
        if len(content) > settings.MAX_FILE_SIZE:
            raise ValueError(f"文件大小超过限制（{settings.MAX_FILE_SIZE}字节）")
        
        # 验证文件类型
        file_type = self._get_file_type(file.filename)
        if file_type not in settings.allowed_file_types_list:
            raise ValueError(f"不支持的文件类型: {file_type}")
        
        # 计算MD5
        md5_hash = self._calculate_md5(content)
        
        # 检查文件是否已存在
        existing_file = await self.db.execute(
            select(File).where(File.md5_hash == md5_hash)
        )
        existing = existing_file.scalar_one_or_none()
        if existing:
            return existing
        
        # 生成存储路径
        storage_path = f"{user_id}/{md5_hash}/{file.filename}"
        
        # 上传到MinIO
        try:
            from io import BytesIO
            file_obj = BytesIO(content)
            file_obj.seek(0)  # 重置文件指针
            self.minio_client.put_object(
                settings.MINIO_BUCKET_NAME,
                storage_path,
                file_obj,
                length=len(content),
                content_type=file.content_type or "application/octet-stream"
            )
        except Exception as e:
            raise ValueError(f"文件上传失败: {str(e)}")
        
        # 创建文件记录
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
        
        # TODO: 触发异步处理任务（解析、向量化）
        
        return file_record
    
    async def batch_upload_files(
        self,
        files: List[UploadFile],
        user_id: int,
        knowledge_base_id: Optional[int] = None
    ) -> List[File]:
        """批量上传文件"""
        results = []
        for file in files:
            try:
                file_record = await self.upload_file(file, user_id, knowledge_base_id)
                results.append(file_record)
            except Exception as e:
                # 记录错误但继续处理其他文件
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
        """下载文件"""
        file = await self.get_file(file_id, user_id)
        if not file:
            return None
        
        try:
            response = self.minio_client.get_object(settings.MINIO_BUCKET_NAME, file.storage_path)
            return {
                "content": response,
                "filename": file.original_filename,
                "content_type": f"application/{file.file_type}"
            }
        except Exception:
            return None

    async def get_file_content(self, file_id: int, user_id: int) -> Optional[bytes]:
        """获取文件原始字节（供解析/切分用，内部调用需已校验权限）"""
        file = await self.get_file(file_id, user_id)
        if not file:
            return None
        try:
            response = self.minio_client.get_object(settings.MINIO_BUCKET_NAME, file.storage_path)
            data = response.read()
            response.close()
            return data
        except Exception:
            return None
