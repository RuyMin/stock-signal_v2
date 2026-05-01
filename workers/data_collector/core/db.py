"""asyncpg 풀 — PERFORMANCE_SKILL §1: Worker는 min=2/max=5."""
import os
import re
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


def _to_asyncpg_dsn(url: str) -> str:
    """DATABASE_URL이 SQLAlchemy 형식이면 asyncpg가 인식할 형식으로 변환."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = _to_asyncpg_dsn(os.environ["DATABASE_URL"])
        _pool = await asyncpg.create_pool(
            dsn,
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
