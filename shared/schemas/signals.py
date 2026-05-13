"""
수급 신호 (signals) 스키마.

worker-data-collector가 한투 OpenAPI에서 수집해 PG에 저장하는 형태.
"""

from datetime import date, datetime
from decimal import Decimal
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

    # Momentum indicators (모멘텀 지표)
    one_day_net_buy: Optional[int] = None  # 1일 순매수 (기관+외국인 합산)
    three_day_avg_net_buy: Optional[int] = None  # 3일 평균 순매수

    # Volume indicators (거래량 지표)
    volume_ratio: Optional[Decimal] = None  # 당일 거래량 / 20일 평균 거래량

    # Technical indicators (기술적 지표)
    rsi: Optional[Decimal] = None  # 14-period RSI (0-100)
    ma_alignment: Optional[str] = None  # 이동평균선 배열: 'bullish' | 'bearish' | 'neutral'
    bollinger_position: Optional[Decimal] = None  # 볼린저밴드 위치 (0-1)
    trading_value: Optional[int] = None  # 거래대금 (원화)

