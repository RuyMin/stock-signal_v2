"""
매크로 5지표 (macro_indicators) 스키마.

worker-data-collector가 yfinance에서 수집. 미국 장 기준 종가.
한국 장 마감 시점에는 직전 미국 거래일 종가가 가장 신선한 데이터.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class MacroSnapshot(BaseModel):
    """일별 매크로 5지표 스냅샷."""

    date: date  # 미국 장 종가 기준일
    us10y: Optional[float] = Field(default=None, description="미국 국채 10년물 금리 (%)")
    dxy: Optional[float] = Field(default=None, description="달러 인덱스")
    wti: Optional[float] = Field(default=None, description="WTI 유가 (USD/배럴)")
    sp500: Optional[float] = Field(default=None, description="S&P500 지수")
    gold: Optional[float] = Field(default=None, description="국제 금 (USD/oz)")
    collected_at: datetime = Field(default_factory=datetime.utcnow)
