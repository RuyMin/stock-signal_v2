"""
보유 종목 (holdings) 스키마.

Backend FastAPI의 holdings CRUD API 계약.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class HoldingCreateRequest(BaseModel):
    """POST /holdings — 보유 종목 추가 요청.

    chat_id는 listener가 텔레그램 사용자별로 보내옴 (소규모 multi-user).
    """

    ticker: str = Field(min_length=6, max_length=10, description="종목코드 (예: 005930)")
    chat_id: int = Field(description="텔레그램 chat_id (사용자 식별)")


class HoldingResponse(BaseModel):
    """단일 보유 종목 응답."""

    id: int
    ticker: str
    name: Optional[str] = None
    added_at: datetime
    user_id: Optional[str] = None

    class Config:
        from_attributes = True


class HoldingListResponse(BaseModel):
    """GET /holdings — 보유 종목 목록 응답."""

    items: list[HoldingResponse]
    total: int
