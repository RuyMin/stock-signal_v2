"""holdings CRUD — 사용자별 보유 종목 관리 (multi-user, 소규모 화이트리스트).

API:
  POST   /holdings                     — 종목 추가 (ticker + chat_id 필수)
  GET    /holdings?chat_id=...         — 사용자별 보유 종목 목록
  DELETE /holdings/{ticker}?chat_id=...  — 사용자별 종목 제거

각 요청은 chat_id로 사용자 식별. listener가 텔레그램 사용자별로 chat_id 전달.
backend는 사용자가 active 상태인지만 검증 (admin 권한은 admin 전용 엔드포인트에서).

단순 PG 쓰기 — Kafka 미경유 (Vibe 원칙: AI/장시간 작업만 Kafka).
"""
import re

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.exceptions import VibeException
from models.holding import Holding
from models.user import User
from schemas.holdings import (  # type: ignore[import-not-found]
    HoldingCreateRequest,
    HoldingListResponse,
    HoldingResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/holdings", tags=["holdings"])

TICKER_PATTERN = re.compile(r"^\d{6}$")


def _validate_ticker(ticker: str) -> None:
    if not TICKER_PATTERN.match(ticker):
        raise VibeException(
            error_code="INVALID_REQUEST",
            message="종목코드는 6자리 숫자입니다",
            status_code=400,
            detail={"ticker": ticker},
        )


async def _resolve_active_user(db: AsyncSession, chat_id: int) -> User:
    """chat_id → active user. pending/inactive/미등록은 거부."""
    q = await db.execute(select(User).where(User.chat_id == chat_id))
    user = q.scalar_one_or_none()
    if user is None:
        raise VibeException(
            error_code="USER_NOT_FOUND",
            message="등록되지 않은 사용자입니다 — 먼저 /start를 호출하세요",
            status_code=404,
            detail={"chat_id": chat_id},
        )
    if user.status != "active":
        raise VibeException(
            error_code="FORBIDDEN",
            message=f"사용자 상태가 '{user.status}'입니다. admin 승인 필요",
            status_code=403,
            detail={"chat_id": chat_id, "status": user.status},
        )
    return user


@router.post("", response_model=HoldingResponse, status_code=status.HTTP_201_CREATED)
async def add_holding(
    payload: HoldingCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> HoldingResponse:
    _validate_ticker(payload.ticker)
    user = await _resolve_active_user(db, payload.chat_id)

    holding = Holding(user_id=user.id, ticker=payload.ticker)
    db.add(holding)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise VibeException(
            error_code="INVALID_REQUEST",
            message="이미 등록된 종목입니다",
            status_code=409,
            detail={"ticker": payload.ticker, "chat_id": payload.chat_id},
        )
    await db.refresh(holding)

    logger.info(
        "holding_added",
        ticker=holding.ticker,
        holding_id=holding.id,
        user_id=user.id,
        chat_id=user.chat_id,
    )
    return HoldingResponse.model_validate(holding)


@router.get("", response_model=HoldingListResponse)
async def list_holdings(
    chat_id: int = Query(..., description="텔레그램 chat_id"),
    db: AsyncSession = Depends(get_db),
) -> HoldingListResponse:
    user = await _resolve_active_user(db, chat_id)
    result = await db.execute(
        select(Holding).where(Holding.user_id == user.id).order_by(Holding.added_at.desc())
    )
    items = [HoldingResponse.model_validate(h) for h in result.scalars().all()]
    return HoldingListResponse(items=items, total=len(items))


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_holding(
    ticker: str,
    chat_id: int = Query(..., description="텔레그램 chat_id"),
    db: AsyncSession = Depends(get_db),
) -> None:
    _validate_ticker(ticker)
    user = await _resolve_active_user(db, chat_id)

    result = await db.execute(
        delete(Holding).where(Holding.user_id == user.id, Holding.ticker == ticker)
    )
    await db.commit()
    if result.rowcount == 0:
        raise VibeException(
            error_code="HOLDING_NOT_FOUND",
            message="등록된 보유 종목이 아닙니다",
            status_code=404,
            detail={"ticker": ticker, "chat_id": chat_id},
        )
    logger.info("holding_removed", ticker=ticker, user_id=user.id, chat_id=user.chat_id)
