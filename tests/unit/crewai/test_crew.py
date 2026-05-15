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
        text = '여기 결과: ```json\n[{"ticker":"005930","sentiment":"positive","macro_verdict":"favorable"}]\n```'
        items = parse(text)
        assert len(items) == 1
        assert items[0]["ticker"] == "005930"

    def test_validate_requires_ticker(self):
        """score/recommendation_type은 코드가 산정 → ticker만 필수. ticker 누락 시 거부."""
        parse = self._items_a()
        text = '[{"sentiment":"positive"}]'
        assert parse(text) == []

    def test_validate_accepts_legacy_score_fields(self):
        """이전 스키마(score/recommendation_type 포함)도 ticker만 있으면 통과 — 필드는 무시됨."""
        parse = self._items_a()
        text = '[{"ticker":"005930","recommendation_type":"foo","score":150}]'
        items = parse(text)
        assert len(items) == 1  # score/type은 코드가 산정해 덮어씀


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
    """on_complete()를 raw_result(LLM 결과 문자열)와 함께 호출해 INSERT 검증.
    score는 LLM 출력 무시 + 코드가 signals + sentiment + macro_verdict로 산정.
    """

    @pytest.mark.asyncio
    async def test_crew_001_full_insert(self, db_pool, crew_with_db):
        """CREW-001: 정상 raw_result → recommendations INSERT (점수 코드 산정)."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        # 005930: 강한 supply(consec 5 + surge) + technical → buy_hedge 구간
        await SignalFactory.create(
            db_pool, target, "005930",
            consecutive_buy_days=5, agency_net_buy=1_000_000_000,
            foreign_net_buy=500_000_000,
            one_day_net_buy=15_000_000_000, three_day_avg_net_buy=5_000_000_000,
            volume_ratio=4.0, rsi=55.0, ma_alignment="bullish",
            bollinger_position=0.5, trading_value=50_000_000_000,
        )
        # 000660: 약한 supply(consec 2) + 평범한 technical → watch 구간
        await SignalFactory.create(
            db_pool, target, "000660",
            consecutive_buy_days=2, agency_net_buy=500_000_000,
            foreign_net_buy=200_000_000,
            volume_ratio=1.0, rsi=55.0, ma_alignment="neutral",
            bollinger_position=0.5, trading_value=20_000_000_000,
        )
        Crew = crew_with_db
        raw = json.dumps([
            {"ticker": "005930", "name": "삼성전자",
             "sentiment": "positive", "macro_verdict": "favorable",
             "reason_supply": "기관 5일 매수 + 급등 모멘텀",
             "reason_news": "호재", "reason_macro": "DXY 하락",
             "estimated_avg_price": 72000},
            {"ticker": "000660", "name": "SK하이닉스",
             "sentiment": "positive", "macro_verdict": "favorable",
             "reason_supply": "외국인 순매수 약함",
             "reason_news": None, "reason_macro": None,
             "estimated_avg_price": None},
        ])
        crew = Crew(job_id="00000000-0000-0000-0000-000000000001")
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(raw, {
            "target_date": target.isoformat(),
            "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 2
        assert result["has_buy_hedge"] is True
        assert result["has_watch"] is True

    @pytest.mark.asyncio
    async def test_crew_002_holding_items_all_inserted(self, db_pool, crew_with_db):
        """CREW-002: 보유 종목 5개 — score 낮아도 exit_alert로 모두 INSERT."""
        from tests.factories import HoldingFactory
        target = date(2026, 4, 28)
        tickers = [f"00593{i}" for i in range(5)]
        for t in tickers:
            await HoldingFactory.create(db_pool, ticker=t)
        Crew = crew_with_db
        # signals row 없음 + sentiment 부정 → 낮은 score → 보유 종목이므로 exit_alert로 들어감
        items = [
            {"ticker": t, "sentiment": "negative", "macro_verdict": "unfavorable"}
            for t in tickers
        ]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 5
        assert result["has_exit_alert"] is True

    @pytest.mark.asyncio
    async def test_crew_003_buy_hedge_score_range(self, db_pool, crew_with_db):
        """CREW-003: signals 강 + sentiment 강긍정 → score >= 70 buy_hedge."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "005930",
            consecutive_buy_days=5, agency_net_buy=2_000_000_000,
            foreign_net_buy=1_000_000_000,
            one_day_net_buy=20_000_000_000, volume_ratio=4.0, rsi=55.0,
            ma_alignment="bullish", bollinger_position=0.5,
            trading_value=100_000_000_000,
        )
        Crew = crew_with_db
        items = [{"ticker": "005930", "sentiment": "strongly_positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        assert result["has_buy_hedge"] is True
        async with db_pool.acquire() as conn:
            score = await conn.fetchval(
                "SELECT score FROM recommendations WHERE ticker='005930'"
            )
        assert score >= 70

    @pytest.mark.asyncio
    async def test_crew_004_watch_score_range(self, db_pool, crew_with_db):
        """CREW-004: signals 중 + sentiment 긍정 → score 50~69 watch."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "005930",
            consecutive_buy_days=2, agency_net_buy=300_000_000,
            foreign_net_buy=100_000_000,
            volume_ratio=1.0, rsi=55.0, ma_alignment="neutral",
            bollinger_position=0.5, trading_value=20_000_000_000,
        )
        Crew = crew_with_db
        items = [{"ticker": "005930", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        assert result["has_watch"] is True
        async with db_pool.acquire() as conn:
            score = await conn.fetchval(
                "SELECT score FROM recommendations WHERE ticker='005930'"
            )
        assert 50 <= score < 70

    @pytest.mark.asyncio
    async def test_crew_005_exit_alert(self, db_pool, crew_with_db):
        """CREW-005: 보유 + signals 없음/약 + 부정 → score<50 exit_alert."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="005930")
        Crew = crew_with_db
        items = [{"ticker": "005930", "sentiment": "negative",
                  "macro_verdict": "unfavorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["has_exit_alert"] is True

    @pytest.mark.asyncio
    async def test_crew_006_buy_hedge_estimated_avg_price(self, db_pool, crew_with_db):
        """CREW-006: buy_hedge일 때만 estimated_avg_price 저장, 그 외는 NULL 강제."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        # 005930 → buy_hedge (강한 신호 + 긍정)
        await SignalFactory.create(
            db_pool, target, "005930",
            consecutive_buy_days=5, agency_net_buy=2_000_000_000,
            foreign_net_buy=1_000_000_000,
            one_day_net_buy=20_000_000_000, volume_ratio=4.0, rsi=55.0,
            ma_alignment="bullish", bollinger_position=0.5,
            trading_value=100_000_000_000,
        )
        # 000660 → watch (중간 신호)
        await SignalFactory.create(
            db_pool, target, "000660",
            consecutive_buy_days=2, agency_net_buy=300_000_000,
            foreign_net_buy=100_000_000,
            volume_ratio=1.0, rsi=55.0, ma_alignment="neutral",
            bollinger_position=0.5, trading_value=20_000_000_000,
        )
        Crew = crew_with_db
        items = [
            {"ticker": "005930", "sentiment": "strongly_positive",
             "macro_verdict": "favorable", "estimated_avg_price": 72000},
            {"ticker": "000660", "sentiment": "positive",
             "macro_verdict": "favorable", "estimated_avg_price": 99999},
        ]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            buy = await conn.fetchval(
                "SELECT estimated_avg_price FROM recommendations WHERE ticker='005930'"
            )
            watch = await conn.fetchval(
                "SELECT estimated_avg_price FROM recommendations WHERE ticker='000660'"
            )
        assert buy == Decimal("72000")
        assert watch is None  # watch는 LLM이 넣어도 코드가 NULL로 강제

    @pytest.mark.asyncio
    async def test_crew_009_inserts_use_job_id(self, db_pool, crew_with_db):
        """CREW-009: 저장 시 crew.job_id 반영."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "005930", consecutive_buy_days=3,
            volume_ratio=1.0, rsi=55.0, ma_alignment="neutral",
            bollinger_position=0.5, trading_value=20_000_000_000,
        )
        Crew = crew_with_db
        job_id = "11111111-1111-1111-1111-111111111111"
        items = [{"ticker": "005930", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew(job_id=job_id)
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
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


class TestOnCompleteClassification:
    """on_complete() 분류 — 코드가 signals + sentiment + macro_verdict로 score 산정 + 분류."""

    @pytest.mark.asyncio
    async def test_low_score_new_candidate_excluded(self, db_pool, crew_with_db):
        """신규 후보(보유 X) score<50 → INSERT 안 함."""
        Crew = crew_with_db
        # signals row 없음 + sentiment 부정 → 낮은 score, 보유 아니므로 제외
        items = [{"ticker": "999111", "sentiment": "negative",
                  "macro_verdict": "unfavorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": "2026-04-28", "target_trading_date": "2026-04-29",
        })
        assert result["recommendation_count"] == 0

    @pytest.mark.asyncio
    async def test_low_score_holding_forced_exit_alert(self, db_pool, crew_with_db):
        """보유 종목 score<50 → exit_alert."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="005930")
        Crew = crew_with_db
        # signals 없음 + sentiment 부정 → score 낮고 보유 → exit_alert
        items = [{"ticker": "005930", "sentiment": "negative",
                  "macro_verdict": "unfavorable"}]
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
        """signals 강 + sentiment 강긍정 → score >= 70 → buy_hedge (보유 여부 무관)."""
        from tests.factories import HoldingFactory, SignalFactory
        target = date(2026, 4, 28)
        await HoldingFactory.create(db_pool, ticker="000660")
        await SignalFactory.create(
            db_pool, target, "000660",
            consecutive_buy_days=5, agency_net_buy=2_000_000_000,
            foreign_net_buy=1_000_000_000,
            one_day_net_buy=20_000_000_000, volume_ratio=4.0, rsi=55.0,
            ma_alignment="bullish", bollinger_position=0.5,
            trading_value=100_000_000_000,
        )
        Crew = crew_with_db
        items = [{"ticker": "000660", "sentiment": "strongly_positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        assert result["has_buy_hedge"] is True
        async with db_pool.acquire() as conn:
            rec_type = await conn.fetchval(
                "SELECT recommendation_type FROM recommendations WHERE ticker='000660'"
            )
        assert rec_type == "buy_hedge"

    @pytest.mark.asyncio
    async def test_mid_score_forced_watch(self, db_pool, crew_with_db):
        """50≤score<70 → watch (보유 무관)."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "999222",
            consecutive_buy_days=2, agency_net_buy=300_000_000,
            foreign_net_buy=100_000_000,
            volume_ratio=1.0, rsi=55.0, ma_alignment="neutral",
            bollinger_position=0.5, trading_value=20_000_000_000,
        )
        Crew = crew_with_db
        items = [{"ticker": "999222", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        result = crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
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
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "018880", consecutive_buy_days=3,
            volume_ratio=1.0, rsi=55.0, ma_alignment="neutral",
            bollinger_position=0.5, trading_value=20_000_000_000,
        )
        Crew = crew_with_db

        from crews.stock_recommendation import crew as crew_mod

        def _fake_kis(ticker):
            return "테스트종목" if ticker == "018880" else None

        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", _fake_kis)

        items = [{"ticker": "018880", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='018880'"
            )
        assert name == "테스트종목"

    @pytest.mark.asyncio
    async def test_llm_name_overrides_kis(self, db_pool, crew_with_db, monkeypatch):
        """LLM이 name 명시 → KIS 호출 안 함."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "005930",
            consecutive_buy_days=5, agency_net_buy=2_000_000_000,
            foreign_net_buy=1_000_000_000,
            one_day_net_buy=20_000_000_000, volume_ratio=4.0, rsi=55.0,
            ma_alignment="bullish", bollinger_position=0.5,
            trading_value=100_000_000_000,
        )
        Crew = crew_with_db
        from crews.stock_recommendation import crew as crew_mod
        kis_calls = {"count": 0}

        def _spy_kis(ticker):
            kis_calls["count"] += 1
            return "다른이름"

        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", _spy_kis)

        items = [{"ticker": "005930", "name": "삼성전자",
                  "sentiment": "strongly_positive", "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='005930'"
            )
        assert name == "삼성전자"
        assert kis_calls["count"] == 0

    @pytest.mark.asyncio
    async def test_name_null_when_kis_fails(self, db_pool, crew_with_db, monkeypatch):
        """LLM 누락 + KIS 실패(None) + 알려진 이름 없음 → name=NULL INSERT."""
        from tests.factories import SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "018880", consecutive_buy_days=2,
            volume_ratio=1.0, trading_value=20_000_000_000,
        )
        Crew = crew_with_db
        from crews.stock_recommendation import crew as crew_mod

        def _fail_kis(ticker):
            return None

        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", _fail_kis)

        items = [{"ticker": "018880", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='018880'"
            )
        assert name is None

    @pytest.mark.asyncio
    async def test_name_falls_back_to_known_recommendations(
        self, db_pool, crew_with_db, monkeypatch
    ):
        """LLM 누락 + KIS 실패 → 과거 recommendations에서 알려진 name 캐시 사용."""
        from tests.factories import RecommendationFactory, SignalFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "093370", consecutive_buy_days=3,
            volume_ratio=1.0, trading_value=20_000_000_000,
        )
        # 과거 다른 날짜에 동일 ticker로 이름이 정상 저장된 경험 있음
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 20), date(2026, 4, 21),
            ticker="093370", name="후성 보통주",
            recommendation_type="watch", score=55,
        )
        Crew = crew_with_db
        from crews.stock_recommendation import crew as crew_mod
        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", lambda t: None)

        items = [{"ticker": "093370", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='093370' "
                "AND target_trading_date='2026-04-29'"
            )
        assert name == "후성 보통주"

    @pytest.mark.asyncio
    async def test_name_falls_back_to_holdings_when_no_recs(
        self, db_pool, crew_with_db, monkeypatch
    ):
        """LLM 누락 + KIS 실패 + 과거 recs 없음 → holdings에 알려진 name 사용."""
        from tests.factories import HoldingFactory, SignalFactory, UserFactory
        target = date(2026, 4, 28)
        await SignalFactory.create(
            db_pool, target, "003690", consecutive_buy_days=2,
            volume_ratio=1.0, trading_value=20_000_000_000,
        )
        user = await UserFactory.create(db_pool, chat_id=999, status="active")
        await HoldingFactory.create(
            db_pool, ticker="003690", name="코리안리재보험보통주",
            user_id=user["id"], chat_id=999,
        )
        Crew = crew_with_db
        from crews.stock_recommendation import crew as crew_mod
        monkeypatch.setattr(crew_mod.kis_api, "fetch_ticker_name", lambda t: None)

        items = [{"ticker": "003690", "sentiment": "positive",
                  "macro_verdict": "favorable"}]
        crew = Crew()
        await _seed_job(db_pool, crew.job_id)
        crew.on_complete(json.dumps(items), {
            "target_date": target.isoformat(), "target_trading_date": "2026-04-29",
        })
        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM recommendations WHERE ticker='003690' "
                "AND target_trading_date='2026-04-29'"
            )
        assert name == "코리안리재보험보통주"


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
    async def test_holdings_query_filters_etf(self, db_pool, reset_psycopg_pool):
        """HoldingsQueryTool은 single_stock만 반환 — ETF/ETN은 일일 사이클에서 제외."""
        from tests.factories import HoldingFactory
        await HoldingFactory.create(db_pool, ticker="005930",
                                     instrument_type="single_stock")
        await HoldingFactory.create(db_pool, ticker="379800",
                                     name="KODEX 미국S&P500",
                                     instrument_type="index_etf")
        await HoldingFactory.create(db_pool, ticker="091160",
                                     name="KODEX 반도체",
                                     instrument_type="sector_etf")

        from crews.stock_recommendation.tools import HoldingsQueryTool
        tool = HoldingsQueryTool()
        out = tool._run()
        parsed = _peel_tool_output(out)
        assert parsed["count"] == 1
        assert set(parsed["tickers"]) == {"005930"}
        # ETF 둘은 결과에 없어야 함
        assert "379800" not in parsed["tickers"]
        assert "091160" not in parsed["tickers"]

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
