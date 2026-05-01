"""data-collector main.py 헬퍼 함수 단위 테스트.

`_ensure_job_row`가 운영 차단 위험(crewai의 recommendations.job_id FK)을 막는지 검증.
실제 main loop는 통합 테스트 영역.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "data_collector")
)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)


class TestEnsureJobRow:
    @pytest.mark.asyncio
    async def test_inserts_when_uuid(self, db_pool):
        """UUID 형식 job_id → jobs row 1건 INSERT."""
        from main import _ensure_job_row
        job_id = str(uuid.uuid4())
        await _ensure_job_row(db_pool, job_id)

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id::text, job_type, status, progress FROM jobs WHERE id = $1::uuid",
                job_id,
            )
        assert row is not None
        assert row["job_type"] == "stock-recommendation"
        assert row["status"] == "in_progress"
        assert row["progress"] == 10

    @pytest.mark.asyncio
    async def test_idempotent_on_conflict(self, db_pool):
        """동일 job_id 두 번 호출해도 충돌 안 발생, row 1건 유지."""
        from main import _ensure_job_row
        job_id = str(uuid.uuid4())
        await _ensure_job_row(db_pool, job_id)
        await _ensure_job_row(db_pool, job_id)

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM jobs WHERE id = $1::uuid", job_id
            )
        assert count == 1

    @pytest.mark.asyncio
    async def test_skips_non_uuid(self, db_pool):
        """수동 트리거의 'manual-...' 같은 비-UUID는 INSERT 시도 안 함."""
        from main import _ensure_job_row
        await _ensure_job_row(db_pool, "manual-1234567890")

        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM jobs")
        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_none(self, db_pool):
        """job_id=None → 조용히 스킵."""
        from main import _ensure_job_row
        await _ensure_job_row(db_pool, None)

        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM jobs")
        assert count == 0

    @pytest.mark.asyncio
    async def test_db_error_does_not_raise(self, db_pool):
        """PG 실패 시(pool 닫힘) 워크플로우 차단 안 함 — warning만 남기고 None 반환."""
        from main import _ensure_job_row
        await db_pool.close()
        # 예외 없이 정상 종료되어야 함
        await _ensure_job_row(db_pool, str(uuid.uuid4()))


class TestNextBusinessDay:
    """premarket fallback에서 target_trading_date 계산용 헬퍼."""

    def test_friday_to_monday(self):
        from datetime import date as _date
        from main import _next_business_day
        # 금요일 → 다음 월요일
        assert _next_business_day(_date(2026, 5, 1)) == _date(2026, 5, 4)

    def test_weekday_to_next_weekday(self):
        from datetime import date as _date
        from main import _next_business_day
        # 목 → 금
        assert _next_business_day(_date(2026, 4, 30)) == _date(2026, 5, 1)
