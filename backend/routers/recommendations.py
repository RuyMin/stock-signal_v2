"""recommendations 조회 — READ only.

API:
  GET /recommendations?date=YYYY-MM-DD     — 특정 날짜 추천
  GET /recommendations/recent?limit=N      — 최근 N일 추천
"""
from datetime import date as date_type

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.recommendation import Recommendation
from schemas.recommendations import (  # type: ignore[import-not-found]
    RecommendationItem,
    RecommendationListResponse,
)

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
