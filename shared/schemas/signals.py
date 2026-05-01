"""
수급 신호 (signals) 스키마.

worker-data-collector가 한투 OpenAPI에서 수집해 PG에 저장하는 형태.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class SignalSnapshot(BaseModel):
    """일별 종목 수급 스냅샷 — signals 테이블 1행에 대응."""

    date: date
    ticker: str = Field(min_length=6, max_length=10)
    agency_buy: int  # 기관 매수액
    agency_sell: int  # 기관 매도액
    agency_net_buy: int  # 기관 순매수 (= buy - sell)
    foreign_buy: int  # 외국인 매수액
    foreign_sell: int  # 외국인 매도액
    foreign_net_buy: int  # 외국인 순매수
    consecutive_buy_days: int = Field(
        default=0,
        description="연속 순매수일 (data-collector가 계산하여 저장)",
    )
    collected_at: datetime = Field(default_factory=datetime.utcnow)
