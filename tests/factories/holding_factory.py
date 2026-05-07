"""테스트용 holdings 데이터 생성 (multi-user 대응)."""
from decimal import Decimal
from typing import Optional


class HoldingFactory:
    @staticmethod
    async def create(
        pool,
        ticker: str = "005930",
        name: Optional[str] = None,
        avg_price: Optional[Decimal] = None,
        user_id: Optional[str] = None,
        chat_id: int = 11111111,
    ) -> dict:
        """user_id가 None이면 chat_id로 user를 만들거나 찾아서 사용한다.

        backward-compat: user_id 지정 안 해도 자동 user 생성으로 동작.
        """
        async with pool.acquire() as conn:
            if user_id is None:
                # 대상 chat_id로 active user를 ensure (이미 있으면 재사용)
                row = await conn.fetchrow(
                    "SELECT id::text FROM users WHERE chat_id = $1", chat_id
                )
                if row is None:
                    row = await conn.fetchrow(
                        "INSERT INTO users (chat_id, status, is_admin) "
                        "VALUES ($1, 'active', FALSE) RETURNING id::text",
                        chat_id,
                    )
                user_id = row["id"]
            row = await conn.fetchrow(
                "INSERT INTO holdings (user_id, ticker, name, avg_price) "
                "VALUES ($1::uuid, $2, $3, $4) "
                "RETURNING id, ticker, name, avg_price, added_at, user_id::text",
                user_id, ticker, name, avg_price,
            )
        return dict(row)
