"""
文件相关API
"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.schemas.file import FileResponse, FileListResponse
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.file_service import FileService

router = APIRouter()


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    knowledge_base_id: int = None,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """上传文件"""
    file_service = FileService(db)
    file_record = await file_service.upload_file(
        file=file,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id
    )
    return file_record


@router.post("/batch-upload", response_model=List[FileResponse], status_code=status.HTTP_201_CREATED)
async def batch_upload_files(
    files: List[UploadFile] = File(...),
    knowledge_base_id: int = None,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """批量上传文件"""
    file_service = FileService(db)
    file_records = await file_service.batch_upload_files(
        files=files,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id
    )
    return file_records


@router.get("", response_model=FileListResponse)
async def get_files(
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取文件列表"""
    file_service = FileService(db)
    result = await file_service.get_files(
        user_id=current_user.id,
        page=page,
        page_size=page_size
    )
    return result


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取文件详情"""
    file_service = FileService(db)
    file_record = await file_service.get_file(file_id, current_user.id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    return file_record


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """删除文件"""
    file_service = FileService(db)
    await file_service.delete_file(file_id, current_user.id)
    return None


@router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """下载文件"""
    file_service = FileService(db)
    file_stream = await file_service.download_file(file_id, current_user.id)
    if not file_stream:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    return StreamingResponse(
        file_stream["content"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_stream["filename"]}"'}
    )
