"""사용자 (users) 스키마 — 텔레그램 봇 multi-user 화이트리스트.

Backend FastAPI의 users CRUD API 계약.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserRegisterRequest(BaseModel):
    """POST /users/register — 텔레그램 listener가 /start 시 호출."""

    chat_id: int = Field(description="텔레그램 chat_id")
    telegram_username: Optional[str] = Field(default=None, max_length=64)


class UserApproveRequest(BaseModel):
    """POST /users/{chat_id}/approve — admin이 신규 사용자 승인."""

    approved_by_chat_id: int = Field(description="승인하는 admin의 chat_id")


class UserResponse(BaseModel):
    id: str
    chat_id: int
    telegram_username: Optional[str] = None
    status: str  # pending | active | inactive
    is_admin: bool
    registered_at: datetime
    approved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
