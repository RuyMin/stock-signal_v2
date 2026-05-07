"""recommendations 조회 — READ only.

API:
  GET /recommendations?date=YYYY-MM-DD                          — 특정 날짜 추천
  GET /recommendations/recent?limit=N                           — 최근 N일 추천
  GET /recommendations/by-ticker/{ticker}?chat_id=N (옵션)      — 해당 종목 자세한 판단 근거
"""
import re
from datetime import date as date_type
from decimal import Decimal
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from clients import kis_api
from core.database import get_db
from core.exceptions import VibeException
from models.holding import Holding
from models.recommendation import Recommendation
from models.user import User
from schemas.recommendations import (  # type: ignore[import-not-found]
    HoldingInfo,
    InstitutionalAvgEstimate,
    MacroSummary,
    NewsBrief,
    RecommendationDetailResponse,
    RecommendationItem,
    RecommendationListResponse,
    SignalSummary,
)

_TICKER_RE = re.compile(r"^\d{6}$")
_SIGNAL_LOOKBACK = 7  # signals/평단가 계산에 쓰는 최대 일자 수
_NEWS_LIMIT = 5

logger = structlog.get_logger()

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

MAX_LIMIT = 100


@router.get("", response_model=RecommendationListResponse)
async def get_recommendations_by_date(
    date: date_type = Query(..., description="추천 발행일 (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
) -> RecommendationListResponse:
    stmt = (
        select(Recommendation)
        .where(Recommendation.date == date)
        .order_by(Recommendation.score.desc())
    )
    result = await db.execute(stmt)
    items = [RecommendationItem.model_validate(r) for r in result.scalars().all()]
    return RecommendationListResponse(items=items, total=len(items), date=date)


@router.get("/recent", response_model=RecommendationListResponse)
async def get_recent_recommendations(
    limit: int = Query(7, ge=1, le=MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
) -> RecommendationListResponse:
    # 최근 limit 개의 발행일을 찾아 그 날짜들의 추천을 모두 반환
    distinct_dates_stmt = (
        select(Recommendation.date)
        .distinct()
        .order_by(Recommendation.date.desc())
        .limit(limit)
    )
    dates_result = await db.execute(distinct_dates_stmt)
    target_dates = [row[0] for row in dates_result.all()]

    if not target_dates:
        return RecommendationListResponse(items=[], total=0, date=None)

    stmt = (
        select(Recommendation)
        .where(Recommendation.date.in_(target_dates))
        .order_by(Recommendation.date.desc(), Recommendation.score.desc())
    )
    result = await db.execute(stmt)
    items = [RecommendationItem.model_validate(r) for r in result.scalars().all()]
    return RecommendationListResponse(items=items, total=len(items), date=None)


@router.get("/by-ticker/{ticker}", response_model=RecommendationDetailResponse)
async def get_recommendation_by_ticker(
    ticker: str,
    chat_id: Optional[int] = Query(None, description="옵션: 보유 정보 표시용"),
    db: AsyncSession = Depends(get_db),
) -> RecommendationDetailResponse:
    """해당 종목의 가장 최근 추천 + 그 사이클의 raw 데이터(시그널/뉴스/매크로) +
    사용자 보유 정보 + 외+기관 추정 평단가."""
    if not _TICKER_RE.match(ticker):
        raise VibeException(
            error_code="INVALID_REQUEST",
            message="종목코드는 6자리 숫자입니다",
            status_code=400,
            detail={"ticker": ticker},
        )

    # 1. 가장 최근 추천 1건
    stmt = (
        select(Recommendation)
        .where(Recommendation.ticker == ticker)
        .order_by(
            Recommendation.target_trading_date.desc(),
            Recommendation.created_at.desc(),
        )
        .limit(1)
    )
    rec = (await db.execute(stmt)).scalar_one_or_none()
    if rec is None:
        raise VibeException(
            error_code="RECOMMENDATION_NOT_FOUND",
            message="해당 종목의 추천 이력이 없습니다",
            status_code=404,
            detail={"ticker": ticker},
        )

    # 2. signals — 최근 N일
    signal_rows = (
        await db.execute(
            text(
                "SELECT date, agency_net_buy, foreign_net_buy, consecutive_buy_days "
                "FROM signals WHERE ticker = :t ORDER BY date DESC LIMIT :lim"
            ),
            {"t": ticker, "lim": _SIGNAL_LOOKBACK},
        )
    ).mappings().all()
    signals = [SignalSummary(**dict(r)) for r in signal_rows]

    # 3. news — recommendation 발행일 ~ target_trading_date 사이, 최신순 N건
    news_rows = (
        await db.execute(
            text(
                "SELECT date, title, url FROM news "
                "WHERE ticker = :t AND date BETWEEN :d_from AND :d_to "
                "ORDER BY date DESC, collected_at DESC LIMIT :lim"
            ),
            {
                "t": ticker, "d_from": rec.date, "d_to": rec.target_trading_date,
                "lim": _NEWS_LIMIT,
            },
        )
    ).mappings().all()
    news = [NewsBrief(**dict(r)) for r in news_rows]

    # 4. macro — target_trading_date 이전 가장 최근 1건
    macro_row = (
        await db.execute(
            text(
                "SELECT date, us10y, dxy, wti, sp500, gold FROM macro_indicators "
                "WHERE date <= :d ORDER BY date DESC LIMIT 1"
            ),
            {"d": rec.target_trading_date},
        )
    ).mappings().first()
    macro = MacroSummary(**dict(macro_row)) if macro_row else None

    # 5. 보유 정보 (chat_id 받았고 active 사용자가 보유 중일 때만)
    holding_info: Optional[HoldingInfo] = None
    if chat_id is not None:
        user = (
            await db.execute(select(User).where(User.chat_id == chat_id))
        ).scalar_one_or_none()
        if user is not None and user.status == "active":
            h = (
                await db.execute(
                    select(Holding).where(
                        Holding.user_id == user.id, Holding.ticker == ticker
                    )
                )
            ).scalar_one_or_none()
            if h is not None:
                holding_info = HoldingInfo(avg_price=h.avg_price, name=h.name)

    # 6. 외+기관 추정 평단가 (signals 양수 매수일 + KIS 일별 종가 가중 평균)
    institutional_avg: Optional[InstitutionalAvgEstimate] = None
    buy_signals = [
        s for s in signals
        if (s.agency_net_buy or 0) + (s.foreign_net_buy or 0) > 0
    ]
    if buy_signals:
        d_from = min(s.date for s in buy_signals)
        d_to = max(s.date for s in buy_signals)
        prices = await kis_api.fetch_daily_prices(ticker, d_from, d_to)
        if prices:
            wsum = 0.0
            qsum = 0
            days = 0
            for s in buy_signals:
                close = prices.get(s.date)
                if close is None:
                    continue
                qty = (s.agency_net_buy or 0) + (s.foreign_net_buy or 0)
                wsum += qty * close
                qsum += qty
                days += 1
            if qsum > 0 and days > 0:
                institutional_avg = InstitutionalAvgEstimate(
                    avg_price=Decimal(round(wsum / qsum, 2)),
                    days=days,
                )

    return RecommendationDetailResponse(
        recommendation=RecommendationItem.model_validate(rec),
        signals=signals,
        news=news,
        macro=macro,
        holding=holding_info,
        institutional_avg=institutional_avg,
    )
