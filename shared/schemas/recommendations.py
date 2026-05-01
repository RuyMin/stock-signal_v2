"""
AI 추천 결과 (recommendations) 스키마.

CrewAI의 SynthesizerAgent가 산출하여 PG에 저장하고,
worker-telegram-notifier가 조회하여 메시지로 송신하는 데이터.

Backend FastAPI의 GET /recommendations 응답 계약이기도 함.
"""

from datetime import date as date_type
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RecommendationType(str, Enum):
    BUY_HEDGE = "buy_hedge"
    WATCH = "watch"
    EXIT_ALERT = "exit_alert"


class RecommendationItem(BaseModel):
    """단일 추천 결과 — recommendations 테이블 1행에 대응."""

    id: int
    date: date_type  # 추천 발행일 (장 마감일)
    target_trading_date: date_type  # 추천 대상 다음 거래일
    ticker: str
    name: Optional[str] = None
    recommendation_type: RecommendationType
    score: int = Field(ge=0, le=100)
    reason_supply: Optional[str] = None  # 수급 패턴 요약
    reason_news: Optional[str] = None  # 뉴스 요약
    reason_macro: Optional[str] = None  # 매크로 환경 코멘트
    estimated_avg_price: Optional[float] = Field(
        default=None,
        description="buy_hedge 단계에서만 산출되는 추정 매집가",
    )
    job_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RecommendationListResponse(BaseModel):
    """GET /recommendations 응답."""

    items: list[RecommendationItem]
    total: int
    date: Optional[date_type] = None  # 단일 날짜 조회 시 해당 날짜
