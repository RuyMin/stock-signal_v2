"""asyncpg 풀 (READ only — recommendations 조회용)."""
import os
import re
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


def _to_asyncpg_dsn(url: str) -> str:
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _to_asyncpg_dsn(os.environ["DATABASE_URL"]),
            min_size=2,
            max_size=5,
            max_inactive_connection_lifetime=300,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
