"""
认证服务（直接使用 bcrypt，避免 passlib 与 bcrypt 版本不兼容）
"""
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.user import User
from app.schemas.auth import UserCreate, UserResponse

# bcrypt 最多 72 字节，超长密码需截断（与注册/登录一致）
BCRYPT_MAX_BYTES = 72


def _truncate_password_72(password: str) -> bytes:
    """将密码截断为 72 字节（UTF-8），返回 bytes 供 bcrypt 使用"""
    b = password.encode("utf-8")
    if len(b) <= BCRYPT_MAX_BYTES:
        return b
    return b[:BCRYPT_MAX_BYTES]


class AuthService:
    """认证服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        try:
            return bcrypt.checkpw(
                _truncate_password_72(plain_password),
                hashed_password.encode("utf-8"),
            )
        except Exception:
            return False
    
    def get_password_hash(self, password: str) -> str:
        """生成密码哈希"""
        return bcrypt.hashpw(
            _truncate_password_72(password),
            bcrypt.gensalt(),
        ).decode("utf-8")
    
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建访问令牌"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        return encoded_jwt
    
    async def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """验证用户"""
        user = await self.get_user_by_username(username)
        if not user:
            return None
        # 迁移补列导致 password_hash 为空时，直接视为密码错误
        if not (user.password_hash and user.password_hash.strip()):
            return None
        if not self.verify_password(password, user.password_hash):
            return None
        return user
    
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()
    
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """根据邮箱获取用户"""
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
    
    async def register_user(self, user_data: UserCreate) -> User:
        """注册用户"""
        # 检查用户名是否已存在
        existing_user = await self.get_user_by_username(user_data.username)
        if existing_user:
            raise ValueError("用户名已存在")
        
        # 检查邮箱是否已存在
        existing_email = await self.get_user_by_email(user_data.email)
        if existing_email:
            raise ValueError("邮箱已存在")
        
        # 创建新用户
        user = User(
            username=user_data.username,
            email=user_data.email,
            password_hash=self.get_password_hash(user_data.password),
            phone=user_data.phone
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def get_current_user(self, token: str) -> User:
        """获取当前用户"""
        credentials_exception = ValueError("无效的认证凭据")
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise credentials_exception
        except JWTError:
            raise credentials_exception
        
        user = await self.get_user_by_username(username)
        if user is None:
            raise credentials_exception
        return user
