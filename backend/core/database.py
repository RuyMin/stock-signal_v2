"""SQLAlchemy async 엔진 + 세션 팩토리.

PERFORMANCE_SKILL §1 권장값:
- pool_size=10
- max_overflow=5
- pool_timeout=30
- pool_recycle=1800
- pool_pre_ping=True

테스트 모드(PYTEST_CURRENT_TEST 또는 BACKEND_USE_NULLPOOL=true)에서는 NullPool 사용 —
pytest_asyncio가 함수 단위로 새 이벤트 루프를 만들면 풀의 캐시된 connection이
"different loop" 오류로 깨지는 이슈 회피.
"""
import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import settings

_in_test_mode = (
    "PYTEST_CURRENT_TEST" in os.environ
    or os.environ.get("BACKEND_USE_NULLPOOL", "").lower() in ("1", "true", "yes")
)

if _in_test_mode:
    engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=5,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=settings.is_dev,
    )

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends — 요청마다 세션 생성/정리."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
