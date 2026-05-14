"""
보유 종목 (holdings) 스키마 + 자산 유형 추론.

Backend FastAPI의 holdings CRUD API 계약 + instrument_type 자동 분류 함수.
"""

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal, Optional

from pydantic import BaseModel, Field

# ─── Instrument type inference ─────────────────────────────────────

InstrumentType = Literal["single_stock", "index_etf", "sector_etf"]

# 새 ETF 브랜드 추가 시 이 두 튜플만 갱신하면 됨.
_ETF_BRAND_PATTERNS: Final[tuple[str, ...]] = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KOSEF",
    "HANARO", "ETN", "ACE", "RISE",
)

_INDEX_KEYWORDS: Final[tuple[str, ...]] = (
    # 영문
    "S&P", "KOSPI", "KOSDAQ", "NASDAQ", "DOW", "CSI", "MSCI",
    # 한글 별칭 (한국 ETF는 한글 표기 다수)
    "코스피", "코스닥", "나스닥", "다우", "지수",
)


def infer_instrument_type(name: Optional[str]) -> InstrumentType:
    """종목명에서 instrument_type 추정 — 순수 함수 (DB 의존 없음).

    분류 규칙:
    - 이름이 None/빈 문자열 → 'single_stock' (보수적 기본값)
    - ETF 브랜드 prefix(KODEX/TIGER 등) 포함 AND 지수 키워드(S&P/KOSPI 등) 포함 → 'index_etf'
    - ETF 브랜드만 포함 → 'sector_etf'
    - 그 외 → 'single_stock'

    예시:
    - "삼성 KODEX 미국S&P500 증권상장지수투자신탁[주식]" → 'index_etf'
    - "TIGER 2차전지테마" → 'sector_etf'
    - "삼성전자보통주" → 'single_stock'
    - None → 'single_stock'
    """
    if not name:
        return "single_stock"
    upper = name.upper()
    has_brand = any(pat in upper for pat in _ETF_BRAND_PATTERNS)
    if not has_brand:
        return "single_stock"
    has_index = any(kw.upper() in upper for kw in _INDEX_KEYWORDS)
    return "index_etf" if has_index else "sector_etf"


# ─── API contracts ─────────────────────────────────────────────────


class HoldingCreateRequest(BaseModel):
    """POST /holdings — 보유 종목 추가 요청.

    chat_id는 listener가 텔레그램 사용자별로 보내옴 (소규모 multi-user).
    name과 avg_price는 사용자가 직접 입력 가능 (선택). name 미지정 시 worker-data-collector가
    KIS API로 채움 (`holdings.name IS NULL`인 row만).
    instrument_type은 등록 시 name 기반 자동 추론 (사용자 명시 안 함).
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
    instrument_type: InstrumentType = "single_stock"

    class Config:
        from_attributes = True


class HoldingListResponse(BaseModel):
    """GET /holdings — 보유 종목 목록 응답."""

    items: list[HoldingResponse]
    total: int
