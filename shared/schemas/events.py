"""
Kafka 이벤트 스키마.

토픽 9개 (`stock.{data,recommendation,notify}.{requested,completed,failed}`)
의 페이로드 계약을 정의.
"""

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class DataCollectionRequested(BaseModel):
    """stock.data.requested — scheduler → worker-data-collector"""

    job_id: str
    target_date: date  # 수집 대상 거래일 (보통 발행 당일)
    triggered_at: datetime = Field(default_factory=datetime.utcnow)


class DataCollectionCompleted(BaseModel):
    """stock.data.completed — worker-data-collector → crewai"""

    job_id: str
    target_date: date
    target_trading_date: date  # 추천 대상 다음 거래일
    signals_count: int
    news_count: int
    macro_collected: bool
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class DataCollectionFailed(BaseModel):
    """stock.data.failed"""

    job_id: str
    target_date: date
    error_code: str
    error_message: str
    failed_at: datetime = Field(default_factory=datetime.utcnow)


class RecommendationCompleted(BaseModel):
    """stock.recommendation.completed — crewai → worker-telegram-notifier"""

    job_id: str
    target_trading_date: date
    recommendation_count: int
    has_buy_hedge: bool
    has_watch: bool
    has_exit_alert: bool
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class RecommendationFailed(BaseModel):
    """stock.recommendation.failed"""

    job_id: str
    error_code: str
    error_message: str
    failed_at: datetime = Field(default_factory=datetime.utcnow)


class NotifyCompleted(BaseModel):
    """stock.notify.completed"""

    job_id: str
    chat_id: str
    message_id: int
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class NotifyFailed(BaseModel):
    """stock.notify.failed"""

    job_id: str
    chat_id: Optional[str]
    error_code: str
    error_message: str
    failed_at: datetime = Field(default_factory=datetime.utcnow)
