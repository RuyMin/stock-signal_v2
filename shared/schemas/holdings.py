"""
보유 종목 (holdings) 스키마.

Backend FastAPI의 holdings CRUD API 계약.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class HoldingCreateRequest(BaseModel):
    """POST /holdings — 보유 종목 추가 요청.

    chat_id는 listener가 텔레그램 사용자별로 보내옴 (소규모 multi-user).
    name과 avg_price는 사용자가 직접 입력 가능 (선택). name 미지정 시 worker-data-collector가
    KIS API로 채움 (`holdings.name IS NULL`인 row만).
    """

    ticker: str = Field(min_length=6, max_length=10, description="종목코드 (예: 005930)")
    chat_id: int = Field(description="텔레그램 chat_id (사용자 식별)")
    name: Optional[str] = Field(
        default=None, max_length=100, description="종목명 (선택, 비우면 worker가 자동 채움)"
    )
    avg_price: Optional[Decimal] = Field(
        default=None, ge=0, description="평단가 (원 단위, 선택)"
    )


class HoldingUpdateRequest(BaseModel):
    """PATCH /holdings/{ticker} — 보유 종목 부분 갱신.

    name과 avg_price를 갱신 가능. None을 명시적으로 보내면 해당 필드 제거 (clear).
    chat_id는 query string으로 전달.
    """

    name: Optional[str] = Field(default=None, max_length=100)
    avg_price: Optional[Decimal] = Field(default=None, ge=0)


class HoldingResponse(BaseModel):
    """단일 보유 종목 응답."""

    id: int
    ticker: str
    name: Optional[str] = None
    avg_price: Optional[Decimal] = None
    added_at: datetime
    user_id: Optional[str] = None

    class Config:
        from_attributes = True


class HoldingListResponse(BaseModel):
    """GET /holdings — 보유 종목 목록 응답."""

    items: list[HoldingResponse]
    total: int
