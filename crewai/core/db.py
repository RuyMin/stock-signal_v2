"""psycopg3 동기 풀.

CrewAI Tool과 BaseCrew.on_complete()는 동기 흐름 — psycopg3 동기 사용.
"""
import os
import re
from typing import Optional

from psycopg_pool import ConnectionPool

_pool: Optional[ConnectionPool] = None


def _to_psycopg_dsn(url: str) -> str:
    """SQLAlchemy 형식이면 평범한 postgresql:// 형식으로 변환."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=_to_psycopg_dsn(os.environ["DATABASE_URL"]),
            min_size=2,
            max_size=5,
            kwargs={"autocommit": False},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
