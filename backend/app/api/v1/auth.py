"""
认证相关API
"""
from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from jose import JWTError, jwt

from app.core.database import get_db
from app.core.config import settings
from app.schemas.auth import Token, UserCreate, UserResponse, UpdatePasswordRequest
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """用户注册"""
    auth_service = AuthService(db)
    try:
        user = await auth_service.register_user(user_data)
        return user
    except ValueError as e:
        msg = str(e)
        if "用户名" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
        if "邮箱" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


@router.post("/login", response_model=Token)
async def login(
    username: str = Form(..., description="用户名"),
    password: str = Form(..., description="密码"),
    db: AsyncSession = Depends(get_db),
):
    """用户登录（使用 Form 避免 FastAPI 0.104 + Pydantic v2 下 OAuth2PasswordRequestForm 的 field_info.in_ 兼容问题）"""
    auth_service = AuthService(db)
    user = await auth_service.authenticate_user(username, password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_service.create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> UserResponse:
    """获取当前用户信息"""
    auth_service = AuthService(db)
    user = await auth_service.get_current_user(token)
    return UserResponse.model_validate(user)


async def get_current_active_user(
    current_user: UserResponse = Depends(get_current_user)
) -> UserResponse:
    """获取当前活跃用户"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="用户未激活")
    return current_user


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserResponse = Depends(get_current_user)):
    """获取当前用户信息"""
    return current_user


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def update_password(
    body: UpdatePasswordRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """修改当前用户密码"""
    auth_service = AuthService(db)
    try:
        await auth_service.update_password(
            current_user.id,
            body.old_password,
            body.new_password,
        )
    except ValueError as e:
        if "原密码错误" in str(e):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
