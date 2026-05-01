"""
뉴스 (news) 스키마.

worker-data-collector가 네이버 금융에서 스크래핑해 PG에 저장하는 형태.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """종목별 뉴스 1건 — news 테이블 1행에 대응."""

    date: date
    ticker: str = Field(min_length=6, max_length=10)
    title: str
    url: Optional[str] = None
    source: str = "naver"
    collected_at: datetime = Field(default_factory=datetime.utcnow)
