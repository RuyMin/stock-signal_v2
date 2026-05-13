"""GET /recommendations + /recent — TEST_SPEC API-006 ~ API-009, API-E007 ~ E009."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.factories import RecommendationFactory


class TestGetRecommendationsByDate:
    """GET /recommendations?date=YYYY-MM-DD — TEST_SPEC §1.4"""

    @pytest.mark.asyncio
    async def test_api_006_existing_date(self, api_client, db_pool):
        """API-006: 추천 존재일 → 200 + items + date."""
        d = date(2026, 4, 28)
        target = date(2026, 4, 29)
        await RecommendationFactory.create(db_pool, d, target, ticker="005930", score=85)
        await RecommendationFactory.create(db_pool, d, target, ticker="000660", score=72)

        resp = await api_client.get("/recommendations", params={"date": d.isoformat()})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["date"] == d.isoformat()
        # score 내림차순
        assert body["items"][0]["score"] >= body["items"][1]["score"]

    @pytest.mark.asyncio
    async def test_api_007_no_recommendation_date(self, api_client, db_pool):
        """API-007: 추천 없는 날짜 → 200 + 빈 items."""
        resp = await api_client.get(
            "/recommendations", params={"date": "2099-01-01"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_api_e007_missing_date(self, api_client, db_pool):
        """API-E007: date 파라미터 누락 → 422 INVALID_REQUEST."""
        resp = await api_client.get("/recommendations")
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_api_e008_invalid_date_format(self, api_client, db_pool):
        """API-E008: date 형식 오류 → 422 INVALID_REQUEST."""
        resp = await api_client.get("/recommendations", params={"date": "2026/04/25"})
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_REQUEST"


class TestGetRecommendationsRecent:
    """GET /recommendations/recent — TEST_SPEC §1.5"""

    @pytest.mark.asyncio
    async def test_api_008_recent_default(self, api_client, db_pool):
        """API-008: limit 기본값 7."""
        # 최근 3일치 추천 데이터
        for offset in range(3):
            d = date(2026, 4, 25) + timedelta(days=offset)
            await RecommendationFactory.create(db_pool, d, d + timedelta(days=1))
        resp = await api_client.get("/recommendations/recent")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3

    @pytest.mark.asyncio
    async def test_api_009_recent_limit_1(self, api_client, db_pool):
        """API-009: limit=1 → 가장 최근 발행일만."""
        for offset in range(3):
            d = date(2026, 4, 25) + timedelta(days=offset)
            await RecommendationFactory.create(db_pool, d, d + timedelta(days=1))
        resp = await api_client.get("/recommendations/recent", params={"limit": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["date"] == "2026-04-27"

    @pytest.mark.asyncio
    async def test_api_e009_invalid_limit(self, api_client, db_pool):
        """API-E009: limit 음수 또는 100 초과 → 422 INVALID_REQUEST."""
        resp1 = await api_client.get("/recommendations/recent", params={"limit": -1})
        resp2 = await api_client.get("/recommendations/recent", params={"limit": 101})
        for resp in (resp1, resp2):
            assert resp.status_code == 422
            assert resp.json()["error_code"] == "INVALID_REQUEST"


class TestGetRecommendationByTicker:
    """GET /recommendations/by-ticker/{ticker} — Detail 응답 (recommendation + signals/news/macro/holding/평단가)."""

    @pytest.mark.asyncio
    async def test_returns_latest_for_ticker(self, api_client, db_pool):
        """동일 ticker 여러 row → target_trading_date 가장 최근 1건이 recommendation에 들어감."""
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), date(2026, 4, 29),
            ticker="005930", score=80,
        )
        await RecommendationFactory.create(
            db_pool, date(2026, 5, 5), date(2026, 5, 6),
            ticker="005930", score=95,
        )

        resp = await api_client.get("/recommendations/by-ticker/005930")
        assert resp.status_code == 200
        body = resp.json()
        rec = body["recommendation"]
        assert rec["ticker"] == "005930"
        assert rec["target_trading_date"] == "2026-05-06"
        assert rec["score"] == 95
        # Detail 응답 키 존재
        assert "signals" in body and "news" in body and "macro" in body
        assert body["holding"] is None  # chat_id 안 줌
        assert body["institutional_avg"] is None  # signals 없음

    @pytest.mark.asyncio
    async def test_404_when_no_history(self, api_client, db_pool):
        """추천 이력 없는 ticker → 404 RECOMMENDATION_NOT_FOUND."""
        resp = await api_client.get("/recommendations/by-ticker/999999")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "RECOMMENDATION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_400_invalid_ticker_format(self, api_client, db_pool):
        """ticker 형식 오류 → 400 INVALID_REQUEST."""
        resp = await api_client.get("/recommendations/by-ticker/abc")
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_includes_signals_news_macro(self, api_client, db_pool):
        """signals/news/macro 데이터가 응답에 포함된다."""
        from tests.factories import SignalFactory
        d_rec = date(2026, 5, 4)
        d_target = date(2026, 5, 6)
        await RecommendationFactory.create(
            db_pool, d_rec, d_target, ticker="005930", score=85,
        )
        # signals 2일치
        await SignalFactory.create(
            db_pool, date(2026, 5, 4), "005930",
            agency_net_buy=1_000_000, foreign_net_buy=2_000_000,
            consecutive_buy_days=2,
        )
        await SignalFactory.create(
            db_pool, date(2026, 4, 30), "005930",
            agency_net_buy=500_000, foreign_net_buy=800_000,
            consecutive_buy_days=1,
        )
        # news 2건 (5/4 ~ 5/6 사이)
        await SignalFactory.create_news(db_pool, date(2026, 5, 4), "005930", title="씨티 하향")
        await SignalFactory.create_news(db_pool, date(2026, 5, 6), "005930", title="반도체 호재")
        # macro 1건 (5/5)
        await SignalFactory.create_macro(db_pool, date(2026, 5, 5))

        resp = await api_client.get("/recommendations/by-ticker/005930")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["signals"]) == 2
        assert {n["title"] for n in body["news"]} == {"씨티 하향", "반도체 호재"}
        assert body["macro"] is not None
        assert body["macro"]["date"] == "2026-05-05"

    @pytest.mark.asyncio
    async def test_holding_when_chat_id_owner(self, api_client, db_pool):
        """chat_id 받았고 사용자 보유 중이면 holding 정보 포함."""
        from tests.factories import HoldingFactory, UserFactory
        chat = 11111111
        await UserFactory.create(db_pool, chat_id=chat, status="active")
        await HoldingFactory.create(
            db_pool, ticker="005930", chat_id=chat,
            avg_price=Decimal("75000"),
        )
        await RecommendationFactory.create(
            db_pool, date(2026, 5, 4), date(2026, 5, 6),
            ticker="005930", score=85,
        )
        resp = await api_client.get(
            "/recommendations/by-ticker/005930", params={"chat_id": chat}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["holding"] is not None
        assert body["holding"]["avg_price"] == "75000.00"

    @pytest.mark.asyncio
    async def test_no_holding_when_user_not_owner(self, api_client, db_pool):
        """chat_id 줬지만 사용자 보유 아니면 holding=None."""
        from tests.factories import UserFactory
        chat = 11111111
        await UserFactory.create(db_pool, chat_id=chat, status="active")
        await RecommendationFactory.create(
            db_pool, date(2026, 5, 4), date(2026, 5, 6),
            ticker="005930", score=85,
        )
        resp = await api_client.get(
            "/recommendations/by-ticker/005930", params={"chat_id": chat}
        )
        body = resp.json()
        assert body["holding"] is None

    @pytest.mark.asyncio
    async def test_consecutive_buy_days_split(self, api_client, db_pool):
        """외인/기관 연속 매수일 분리 계산.
        가장 최근(5/6)에 외인 +, 기관 -. 그 전 5/4 외인 +, 기관 +. 4/30 외인 +, 기관 +.
        → 외국인 3일 연속, 기관 0일 (5/6에서 끊김).
        """
        from tests.factories import SignalFactory
        await RecommendationFactory.create(
            db_pool, date(2026, 5, 4), date(2026, 5, 6),
            ticker="005930", score=85,
        )
        await SignalFactory.create(
            db_pool, date(2026, 5, 6), "005930",
            agency_net_buy=-100_000, foreign_net_buy=14_000_000,
            consecutive_buy_days=3,
        )
        await SignalFactory.create(
            db_pool, date(2026, 5, 4), "005930",
            agency_net_buy=1_700_000, foreign_net_buy=4_400_000,
            consecutive_buy_days=2,
        )
        await SignalFactory.create(
            db_pool, date(2026, 4, 30), "005930",
            agency_net_buy=200_000, foreign_net_buy=1_300_000,
            consecutive_buy_days=1,
        )

        resp = await api_client.get("/recommendations/by-ticker/005930")
        body = resp.json()
        assert body["foreign_consecutive_buy_days"] == 3
        assert body["agency_consecutive_buy_days"] == 0  # 5/6에서 음수로 끊김

    @pytest.mark.asyncio
    async def test_consecutive_buy_days_none_when_no_signals(self, api_client, db_pool):
        """signals 0건이면 두 값 모두 None."""
        await RecommendationFactory.create(
            db_pool, date(2026, 5, 4), date(2026, 5, 6),
            ticker="005930", score=85,
        )
        resp = await api_client.get("/recommendations/by-ticker/005930")
        body = resp.json()
        assert body["foreign_consecutive_buy_days"] is None
        assert body["agency_consecutive_buy_days"] is None
