"""테스트용 users 데이터 생성."""
from typing import Optional


class UserFactory:
    @staticmethod
    async def create(
        pool,
        chat_id: int = 11111111,
        status: str = "active",
        is_admin: bool = False,
        telegram_username: Optional[str] = None,
    ) -> dict:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO users (chat_id, telegram_username, status, is_admin) "
                "VALUES ($1, $2, $3, $4) "
                "RETURNING id::text, chat_id, status, is_admin",
                chat_id, telegram_username, status, is_admin,
            )
        return dict(row)
