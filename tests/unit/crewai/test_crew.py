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


class TestOnCompleteEnforceClassification:
    """on_complete() 분류 후처리 강제 — LLM 룰 일탈 방어."""

    @pytest.mark.asyncio
    async def test_low_score_new_candidate_excluded(self, db_pool, crew_with_db):
        """신규 후보(보유 X) score<50 → INSERT 안 함."""
        Crew = crew_with_db
        # 005930은 보유 안 함 (HoldingFactory 미사용). LLM이 buy_hedge로 잘못 분류해도 제외.
        items = [{"ticker": "999111", "recommendation_type": "buy_hedge", "score": 30}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 0

    @pytest.mark.asyncio
    async def test_low_score_holding_forced_exit_alert(self, db_pool, crew_with_db):
        """보유 종목 score<50 → LLM이 watch로 잘못 분류해도 exit_alert 강제."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="005930")
        Crew = crew_with_db
        items = [{"ticker": "005930", "recommendation_type": "watch", "score": 32}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_exit_alert"] is True
        async with db_pool.acquire() as conn:
            rec_type = await conn.fetchval(
                "SELECT recommendation_type FROM recommendations WHERE ticker='005930'"
            )
        assert rec_type == "exit_alert"

    @pytest.mark.asyncio
    async def test_high_score_forced_buy_hedge(self, db_pool, crew_with_db):
        """score≥70 → LLM이 exit_alert로 잘못 분류해도 buy_hedge 강제."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="000660")
        Crew = crew_with_db
        # LLM이 73점에 exit_alert 분류 (5차 검증에서 실제 발생한 룰 위반)
        items = [{"ticker": "000660", "recommendation_type": "exit_alert", "score": 73}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_buy_hedge"] is True
        async with db_pool.acquire() as conn:
            rec_type = await conn.fetchval(
                "SELECT recommendation_type FROM recommendations WHERE ticker='000660'"
            )
        assert rec_type == "buy_hedge"

    @pytest.mark.asyncio
    async def test_mid_score_forced_watch(self, db_pool, crew_with_db):
        """50≤score<70 → 자동 watch (보유 무관)."""
        Crew = crew_with_db
        items = [{"ticker": "999222", "recommendation_type": "buy_hedge", "score": 55}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_watch"] is True
        async with db_pool.acquire() as conn:
            rec_type = await conn.fetchval(
                "SELECT recommendation_type FROM recommendations WHERE ticker='999222'"
            )
        assert rec_type == "watch"

    @pytest.mark.asyncio
    async def test_name_fills_from_kis_when_llm_omits(
        self, db_pool, crew_with_db, monkeypatch
    ):
        """LLM이 name 누락 → KIS API로 즉시 채워서 INSERT."""
        Crew = crew_with_db

        from crews.stock_recommendation import crew as crew_mod

        def _fake_kis(ticker):
            return "테스트종목" if ticker == "018880" else None

        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", _fake_kis)

        items = [{"ticker": "018880", "recommendation_type": "watch", "score": 60}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='018880'"
            )
        assert name == "테스트종목"

    @pytest.mark.asyncio
    async def test_llm_name_overrides_kis(self, db_pool, crew_with_db, monkeypatch):
        """LLM이 name 명시 → KIS 호출 안 함."""
        Crew = crew_with_db
        from crews.stock_recommendation import crew as crew_mod
        kis_calls = {"count": 0}

        def _spy_kis(ticker):
            kis_calls["count"] += 1
            return "다른이름"

        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", _spy_kis)

        items = [{"ticker": "005930", "name": "삼성전자",
                  "recommendation_type": "buy_hedge", "score": 80}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='005930'"
            )
        assert name == "삼성전자"
        assert kis_calls["count"] == 0

    @pytest.mark.asyncio
    async def test_name_null_when_kis_fails(self, db_pool, crew_with_db, monkeypatch):
        """LLM 누락 + KIS 실패(None) → name=NULL INSERT (notifier가 holdings 폴백 시도)."""
        Crew = crew_with_db
        from crews.stock_recommendation import crew as crew_mod

        def _fail_kis(ticker):
            return None

        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", _fail_kis)

        items = [{"ticker": "018880", "recommendation_type": "watch", "score": 60}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='018880'"
            )
        assert name is None


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
    async def test_signal_query_tickers_filter(self, db_pool, reset_psycopg_pool):
        """SignalQueryTool — tickers 인자로 보유 종목 강도 평가 (consecutive=2도 반환)."""
        from tests.factories import SignalFactory
        target = date(2026, 5, 4)
        await SignalFactory.create(db_pool, target, "005930", consecutive_buy_days=2)
        await SignalFactory.create(db_pool, target, "000660", consecutive_buy_days=5)
        await SignalFactory.create(db_pool, target, "003690", consecutive_buy_days=1)

        from crews.stock_recommendation.tools import SignalQueryTool
        tool = SignalQueryTool()
        # min_consecutive=0 + tickers 지정 → 보유 종목 모두 반환
        out = tool._run(
            target_date=target.isoformat(),
            min_consecutive=0,
            tickers=["005930", "003690"],
        )
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 2
        tickers_returned = {r["ticker"] for r in parsed["items"]}
        assert tickers_returned == {"005930", "003690"}
        # 000660은 미요청이라 제외
        assert "000660" not in tickers_returned

    @pytest.mark.asyncio
    async def test_crew_007_news_query_tool(self, db_pool, reset_psycopg_pool):
        """CREW-007: NewsQueryTool — 지정 종목의 뉴스만 반환 (단일 날짜 범위)."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create_news(db_pool, target, "005930", title="삼성 호재")
        await SignalFactory.create_news(db_pool, target, "000660", title="SK 뉴스")

        from crews.stock_recommendation.tools import NewsQueryTool
        tool = NewsQueryTool()
        out = tool._run(
            date_from=target.isoformat(),
            date_to=target.isoformat(),
            tickers=["005930"],
        )
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 1
        assert parsed["items"][0]["title"] == "삼성 호재"
        assert parsed["items"][0]["date"] == target.isoformat()

    @pytest.mark.asyncio
    async def test_crew_007_news_query_tool_date_range(self, db_pool, reset_psycopg_pool):
        """NewsQueryTool — 날짜 범위에 걸쳐 휴장일 갭 뉴스 모두 반환."""
        from tests.factories import SignalFactory
        signal_date = date(2026, 5, 4)  # 월
        target_trading = date(2026, 5, 6)  # 수 (5/5 어린이날 휴장 끼어 있음)
        await SignalFactory.create_news(
            db_pool, signal_date, "005930", title="씨티 목표가 하향"
        )
        await SignalFactory.create_news(
            db_pool, target_trading, "005930", title="반도체 랠리 호재"
        )

        from crews.stock_recommendation.tools import NewsQueryTool
        tool = NewsQueryTool()
        out = tool._run(
            date_from=signal_date.isoformat(),
            date_to=target_trading.isoformat(),
            tickers=["005930"],
        )
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 2
        dates = {item["date"] for item in parsed["items"]}
        assert dates == {"2026-05-04", "2026-05-06"}

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
