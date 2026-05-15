"""테스트용 recommendations 데이터 생성."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID


class RecommendationFactory:
    @staticmethod
    async def create(
        pool,
        d: date,
        target_trading_date: date,
        ticker: str = "005930",
        recommendation_type: str = "buy_hedge",
        score: int = 80,
        name: Optional[str] = None,
        job_id: Optional[UUID] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        async with pool.acquire() as conn:
            if created_at is not None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO recommendations (
                        date, target_trading_date, ticker, name,
                        recommendation_type, score,
                        reason_supply, reason_news, reason_macro,
                        job_id, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    d, target_trading_date, ticker, name,
                    recommendation_type, score,
                    "기관 5일 연속 순매수",
                    "긍정 뉴스",
                    "매크로 우호",
                    job_id, created_at,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO recommendations (
                        date, target_trading_date, ticker, name,
                        recommendation_type, score,
                        reason_supply, reason_news, reason_macro,
                        job_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    d, target_trading_date, ticker, name,
                    recommendation_type, score,
                    "기관 5일 연속 순매수",
                    "긍정 뉴스",
                    "매크로 우호",
                    job_id,
                )
        return row["id"]
