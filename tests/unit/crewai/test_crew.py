"""StockRecommendationCrew 단위 테스트 — TEST_SPEC CREW-001~009, CREW-E001~004.

LLM 호출은 mock. _parse_recommendations / on_complete / 각 Tool을 직접 검증.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

_CREWAI_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "crewai")
)
if _CREWAI_ROOT not in sys.path:
    sys.path.insert(0, _CREWAI_ROOT)


# ─── psycopg 풀 정리(각 테스트마다) ────────────────────────────────


@pytest.fixture
def reset_psycopg_pool():
    """crewai 의 psycopg 동기 풀이 모듈 싱글톤이라 테스트마다 초기화."""
    yield
    from core.db import close_pool
    close_pool()


# ─── _parse_recommendations 단위 테스트 ────────────────────────────


class TestParseRecommendations:
    def _items_a(self):
        from crews.stock_recommendation.crew import _parse_recommendations
        return _parse_recommendations

    def test_crew_e001_invalid_json_returns_empty(self):
        """CREW-E001: LLM 출력이 JSON 아님 → 빈 리스트 (상위에서 retry)."""
        parse = self._items_a()
        assert parse("garbage no json here") == []

    def test_crew_e001_partial_text_with_json_block(self):
        """JSON 코드블록 추출."""
        parse = self._items_a()
        text = '여기 결과: ```json\n[{"ticker":"005930","recommendation_type":"buy_hedge","score":85}]\n```'
        items = parse(text)
        assert len(items) == 1
        assert items[0]["ticker"] == "005930"

    def test_validate_filters_invalid_score(self):
        parse = self._items_a()
        text = '[{"ticker":"005930","recommendation_type":"buy_hedge","score":150}]'
        # score 150은 0~100 범위 밖 → 거부
        assert parse(text) == []

    def test_validate_filters_invalid_type(self):
        parse = self._items_a()
        text = '[{"ticker":"005930","recommendation_type":"foo","score":80}]'
        assert parse(text) == []


# ─── on_complete (recommendations INSERT) ─────────────────────────


@pytest_asyncio.fixture
async def crew_with_db(db_pool, reset_psycopg_pool):
    """db_pool은 asyncpg(테스트DB 트런케이트용), crewai는 psycopg sync pool 사용 → 같은 DB."""
    from crews.stock_recommendation.crew import StockRecommendationCrew
    yield StockRecommendationCrew


async def _seed_job(db_pool, job_id: str) -> None:
    """recommendations.job_id FK → jobs.id 충족용. 각 테스트가 on_complete 호출 전에 사용."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, job_type, status, progress) "
            "VALUES ($1::uuid, 'stock-recommendation', 'in_progress', 50) "
            "ON CONFLICT DO NOTHING",
            job_id,
        )


class TestOnComplete:
    """on_complete()를 raw_result(LLM 결과 문자열)와 함께 호출해 INSERT 검증."""

    @pytest.mark.asyncio
    async def test_crew_001_full_insert(self, db_pool, crew_with_db):
        """CREW-001: 정상 raw_result → recommendations INSERT."""
        Crew = crew_with_db
        raw = json.dumps([
            {"ticker": "005930", "name": "삼성전자",
             "recommendation_type": "buy_hedge", "score": 85,
             "reason_supply": "기관 5일 매수", "reason_news": "호재",
             "reason_macro": "DXY 하락", "estimated_avg_price": 72000},
            {"ticker": "000660", "name": "SK하이닉스",
             "recommendation_type": "watch", "score": 60,
             "reason_supply": "외국인 순매수", "reason_news": None,
             "reason_macro": None, "estimated_avg_price": None},
        ])
        crew = Crew(job_id="00000000-0000-0000-0000-000000000001")
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(raw, {
            "target_date": "2026-04-28",
            "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 2
        assert result["has_buy_hedge"] is True
        assert result["has_watch"] is True
        assert result["has_exit_alert"] is False

        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM recommendations")
        assert count == 2

    @pytest.mark.asyncio
    async def test_crew_002_max_5_items(self, db_pool, crew_with_db):
        """CREW-002: 5종목 한도 — parser는 5개를 그대로 받아 INSERT한다(한도는 LLM prompt 책임)."""
        Crew = crew_with_db
        items = [
            {"ticker": f"00593{i}", "recommendation_type": "watch", "score": 60}
            for i in range(5)
        ]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 5

    @pytest.mark.asyncio
    async def test_crew_003_buy_hedge_score_range(self, db_pool, crew_with_db):
        """CREW-003: score 70+ 추천 종목 INSERT 가능."""
        Crew = crew_with_db
        items = [{"ticker": "005930", "recommendation_type": "buy_hedge", "score": 88}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_buy_hedge"] is True
        async with db_pool.acquire() as conn:
            score = await conn.fetchval(
                "SELECT score FROM recommendations WHERE ticker='005930'"
            )
        assert score == 88

    @pytest.mark.asyncio
    async def test_crew_004_watch_score_range(self, db_pool, crew_with_db):
        """CREW-004: watch 단계 score 50~69."""
        Crew = crew_with_db
        items = [{"ticker": "005930", "recommendation_type": "watch", "score": 60}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_watch"] is True

    @pytest.mark.asyncio
    async def test_crew_005_exit_alert(self, db_pool, crew_with_db):
        """CREW-005: exit_alert 단계 INSERT 가능 (실제 보유 종목 한정 책임은 LLM)."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="005930")
        Crew = crew_with_db
        items = [{"ticker": "005930", "recommendation_type": "exit_alert", "score": 30}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_exit_alert"] is True

    @pytest.mark.asyncio
    async def test_crew_006_buy_hedge_estimated_avg_price(self, db_pool, crew_with_db):
        """CREW-006: 매수 헬지 estimated_avg_price 저장 + 다른 단계는 None."""
        Crew = crew_with_db
        items = [
            {"ticker": "005930", "recommendation_type": "buy_hedge",
             "score": 80, "estimated_avg_price": 72000},
            {"ticker": "000660", "recommendation_type": "watch", "score": 60},
        ]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            buy = await conn.fetchval(
                "SELECT estimated_avg_price FROM recommendations WHERE ticker='005930'"
            )
            watch = await conn.fetchval(
                "SELECT estimated_avg_price FROM recommendations WHERE ticker='000660'"
            )
        assert buy == Decimal("72000")
        assert watch is None

    @pytest.mark.asyncio
    async def test_crew_009_inserts_use_job_id(self, db_pool, crew_with_db):
        """CREW-009: 저장 시 crew.job_id 반영."""
        Crew = crew_with_db
        job_id = "11111111-1111-1111-1111-111111111111"
        items = [{"ticker": "005930", "recommendation_type": "watch", "score": 55}]
        crew = Crew(job_id=job_id)
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT job_id::text FROM recommendations WHERE ticker='005930'"
            )
        assert row == job_id

    @pytest.mark.asyncio
    async def test_crew_e003_no_signals_zero_count(self, db_pool, crew_with_db):
        """CREW-E003: 빈 raw_result → recommendation_count=0 + count=0 발행."""
        Crew = crew_with_db
        crew = Crew()
        result = crew.on_complete("[]", {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 0
        assert result["has_buy_hedge"] is False


# ─── Tool 단위 테스트 ──────────────────────────────────────────────


class TestTools:
    @pytest.mark.asyncio
    async def test_crew_007_signal_query_tool(self, db_pool, reset_psycopg_pool):
        """CREW-007: SignalQueryTool — 3일 이상 연속 종목만 반환."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(db_pool, target, "005930", consecutive_buy_days=5)
        await SignalFactory.create(db_pool, target, "000660", consecutive_buy_days=2)

        from crews.stock_recommendation.tools import SignalQueryTool
        tool = SignalQueryTool()
        out = tool._run(target_date=target.isoformat(), min_consecutive=3)
        # ok() wrapper 형식: {"status":"success", "data": "..."}
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 1
        assert parsed["items"][0]["ticker"] == "005930"

    @pytest.mark.asyncio
    async def test_crew_007_news_query_tool(self, db_pool, reset_psycopg_pool):
        """CREW-007: NewsQueryTool — 지정 종목의 뉴스만 반환."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create_news(db_pool, target, "005930", title="삼성 호재")
        await SignalFactory.create_news(db_pool, target, "000660", title="SK 뉴스")

        from crews.stock_recommendation.tools import NewsQueryTool
        tool = NewsQueryTool()
        out = tool._run(target_date=target.isoformat(), tickers=["005930"])
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 1
        assert parsed["items"][0]["title"] == "삼성 호재"

    @pytest.mark.asyncio
    async def test_crew_007_macro_query_tool(self, db_pool, reset_psycopg_pool):
        """CREW-007: MacroQueryTool — 지정일 이전의 가장 최근 row 반환."""
        from tests.factories import SignalFactory
        await SignalFactory.create_macro(db_pool, date(2026, 4, 28))
        from crews.stock_recommendation.tools import MacroQueryTool
        tool = MacroQueryTool()
        out = tool._run(near_date="2026-04-29")
        parsed = _peel_tool_output(out)
        assert parsed["available"] is True
        assert parsed["us10y"] == 4.2

    @pytest.mark.asyncio
    async def test_crew_008_holdings_query_tool(self, db_pool, reset_psycopg_pool):
        """CREW-008: HoldingsQueryTool — holdings 테이블 조회."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="005930")
        await HoldingFactory.create(db_pool, ticker="000660")

        from crews.stock_recommendation.tools import HoldingsQueryTool
        tool = HoldingsQueryTool()
        out = tool._run()
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 2
        assert set(parsed["tickers"]) == {"005930", "000660"}

    @pytest.mark.asyncio
    async def test_crew_e002_tool_db_failure_returns_error_string(
        self, monkeypatch, reset_psycopg_pool
    ):
        """CREW-E002: Tool DB 실패 → BaseTool err_* 형식 문자열 반환(예외 미전파)."""
        from crews.stock_recommendation import tools as tools_module

        class _BadPool:
            def connection(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(tools_module, "get_pool", lambda: _BadPool())
        from crews.stock_recommendation.tools import HoldingsQueryTool
        tool = HoldingsQueryTool()
        out = tool._run()
        # err_unknown은 status="error" 형태의 문자열을 반환해야 함
        assert "error" in out.lower() or "fail" in out.lower() or "db down" in out


class TestOnCompleteErrors:
    @pytest.mark.asyncio
    async def test_crew_e004_pg_connection_failure(self, monkeypatch):
        """CREW-E004: PG 연결 실패 → on_complete가 예외 전파(상위 main.py가 DLQ)."""
        from crews.stock_recommendation import crew as crew_module

        class _BadPool:
            def connection(self):
                raise RuntimeError("pg down")

        monkeypatch.setattr(crew_module, "get_pool", lambda: _BadPool())
        from crews.stock_recommendation.crew import StockRecommendationCrew
        items = [{"ticker": "005930", "recommendation_type": "watch", "score": 55}]
        c = StockRecommendationCrew()
        with pytest.raises(RuntimeError, match="pg down"):
            c.on_complete(json.dumps(items), {
                "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
            })


# ─── helpers ───────────────────────────────────────────────────────


def _peel_tool_output(raw: str) -> dict:
    """VibeBaseTool.ok()는 'success: <payload>' 접두사 형식. 내부 JSON을 풀어준다."""
    if raw.startswith("success: "):
        body = raw[len("success: "):]
    else:
        body = raw
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": raw}
