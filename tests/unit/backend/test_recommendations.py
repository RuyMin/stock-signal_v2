"""GET /recommendations + /recent — TEST_SPEC API-006 ~ API-009, API-E007 ~ E009."""
from datetime import date, timedelta

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
