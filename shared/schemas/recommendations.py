"""
AI 추천 결과 (recommendations) 스키마.

CrewAI의 SynthesizerAgent가 산출하여 PG에 저장하고,
worker-telegram-notifier가 조회하여 메시지로 송신하는 데이터.

Backend FastAPI의 GET /recommendations 응답 계약이기도 함.
"""

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("job_id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v: Any) -> Optional[str]:
        # SQLAlchemy가 UUID(as_uuid=True) 컬럼을 uuid.UUID 객체로 반환 → str로 강제 변환
        return str(v) if v is not None else None

    class Config:
        from_attributes = True


class RecommendationListResponse(BaseModel):
    """GET /recommendations 응답."""

    items: list[RecommendationItem]
    total: int
    date: Optional[date_type] = None  # 단일 날짜 조회 시 해당 날짜


# ─── Detail 응답 (GET /recommendations/by-ticker/{ticker}) ────────────


class SignalSummary(BaseModel):
    """signals 테이블 단일 행 요약."""

    date: date_type
    agency_net_buy: Optional[int] = None
    foreign_net_buy: Optional[int] = None
    consecutive_buy_days: Optional[int] = None


class NewsBrief(BaseModel):
    """news 테이블 단일 행 요약."""

    date: date_type
    title: str
    url: Optional[str] = None


class MacroSummary(BaseModel):
    """macro_indicators 단일 행."""

    date: date_type
    us10y: Optional[float] = None
    dxy: Optional[float] = None
    wti: Optional[float] = None
    sp500: Optional[float] = None
    gold: Optional[float] = None


class HoldingInfo(BaseModel):
    """요청 사용자의 보유 정보 (chat_id 받았고 보유 중일 때만)."""

    avg_price: Optional[Decimal] = None
    name: Optional[str] = None


class InstitutionalAvgEstimate(BaseModel):
    """외국인+기관 net_buy 가중 종가 평균 (추정).

    SUM(net_buy_i × close_i) / SUM(net_buy_i). consecutive_buy_days 동안의 양수 매수일만 사용.
    실제 매수가가 아닌 net 포지션 변화 기반 추정값이라는 한계는 메시지에 표기.
    """

    avg_price: Decimal
    days: int  # 평균에 사용된 매수 일자 수


class RecommendationDetailResponse(BaseModel):
    """GET /recommendations/by-ticker/{ticker} 응답.

    가장 최근 추천 + 그 사이클의 raw 데이터(시그널/뉴스/매크로) + 사용자 보유 정보 + 추정 평단가.
    chat_id 미지정 시 holding은 None.
    """

    recommendation: RecommendationItem
    signals: list[SignalSummary] = []
    news: list[NewsBrief] = []
    macro: Optional[MacroSummary] = None
    holding: Optional[HoldingInfo] = None
    institutional_avg: Optional[InstitutionalAvgEstimate] = None
