"""
文件相关API
"""
from urllib.parse import quote
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.schemas.file import FileResponse, FileListResponse
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.api.deps import get_client_ip, require_upload_rate_limit
from app.services.file_service import FileService
from app.services.audit_service import log_audit

router = APIRouter()


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    knowledge_base_id: int = None,
    current_user: UserResponse = Depends(require_upload_rate_limit),
    db: AsyncSession = Depends(get_db)
):
    """上传文件"""
    file_service = FileService(db)
    file_record = await file_service.upload_file(
        file=file,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id
    )
    await log_audit(db, current_user.id, "upload_file", "file", str(file_record.id), {"filename": file_record.original_filename}, get_client_ip(request), getattr(request.state, "request_id", None))
    return file_record


@router.post("/batch-upload", response_model=List[FileResponse], status_code=status.HTTP_201_CREATED)
async def batch_upload_files(
    request: Request,
    files: List[UploadFile] = File(...),
    knowledge_base_id: int = None,
    on_duplicate: str = Query("use_existing", description="同 MD5 时：use_existing=返回已有，overwrite=覆盖并清空分块"),
    current_user: UserResponse = Depends(require_upload_rate_limit),
    db: AsyncSession = Depends(get_db)
):
    """批量上传文件"""
    file_service = FileService(db)
    file_records = await file_service.batch_upload_files(
        files=files,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
        on_duplicate=on_duplicate,
    )
    ip = get_client_ip(request)
    for rec in file_records:
        await log_audit(db, current_user.id, "upload_file", "file", str(rec.id), {"filename": rec.original_filename}, ip, getattr(request.state, "request_id", None))
    return file_records


@router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """下载/预览文件（图片会返回正确 Content-Type 便于展示）"""
    file_service = FileService(db)
    file_stream = await file_service.download_file(file_id, current_user.id)
    if not file_stream:
        raise HTTPException(status_code=404, detail="文件不存在")
    filename = file_stream["filename"] or "download"
    # 使用 RFC 5987 编码，避免中文等非 ASCII 导致 latin-1 报错
    encoded_filename = quote(filename, safe="")
    return Response(
        content=file_stream["content"],
        media_type=file_stream["content_type"],
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
        },
    )


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
    request: Request,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """删除文件"""
    file_service = FileService(db)
    await file_service.delete_file(file_id, current_user.id)
    await log_audit(db, current_user.id, "delete_file", "file", str(file_id), None, get_client_ip(request), getattr(request.state, "request_id", None))
    return None
