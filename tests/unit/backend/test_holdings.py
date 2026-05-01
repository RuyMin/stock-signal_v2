"""POST/GET/DELETE /holdings — TEST_SPEC API-001~005, API-E001~E006.

multi-user 전환(2026-04-30): 모든 호출에 chat_id 파라미터 필수.
backend는 chat_id로 active user 조회 → user_id 기반으로 holdings 처리.
"""
import pytest
import pytest_asyncio

from tests.factories import HoldingFactory, UserFactory


CHAT_ID = 11111111  # 테스트용 active user의 chat_id


@pytest_asyncio.fixture(autouse=True)
async def _seed_active_user(db_pool):
    """모든 holdings 테스트는 active user 1명을 미리 시드한다."""
    await UserFactory.create(db_pool, chat_id=CHAT_ID, status="active", is_admin=False)


class TestPostHolding:
    """POST /holdings — TEST_SPEC §1.1"""

    @pytest.mark.asyncio
    async def test_api_001_valid_ticker_added(self, api_client, db_pool):
        """API-001: 유효한 종목코드 추가 → 201 + HoldingResponse."""
        resp = await api_client.post(
            "/holdings", json={"ticker": "005930", "chat_id": CHAT_ID}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["ticker"] == "005930"
        assert "id" in body and "added_at" in body
        # name은 worker가 채우므로 backend POST 시점에는 null
        assert body["name"] is None

    @pytest.mark.asyncio
    async def test_api_e001_missing_ticker(self, api_client, db_pool):
        """API-E001: ticker 필드 누락 → 422 INVALID_REQUEST."""
        resp = await api_client.post("/holdings", json={"chat_id": CHAT_ID})
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_api_e002_invalid_format_alpha(self, api_client, db_pool):
        """API-E002: ticker 형식 오류(영문) → 400 INVALID_REQUEST."""
        resp = await api_client.post(
            "/holdings", json={"ticker": "ABC123", "chat_id": CHAT_ID}
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_api_e003_invalid_format_short(self, api_client, db_pool):
        """API-E003: ticker 5자리 → 400 또는 422 INVALID_REQUEST."""
        resp = await api_client.post(
            "/holdings", json={"ticker": "00593", "chat_id": CHAT_ID}
        )
        # Pydantic min_length=6 검증 → 422
        assert resp.status_code in (400, 422)
        assert resp.json()["error_code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_api_e004_duplicate_ticker(self, api_client, db_pool):
        """API-E004: 동일 종목 재추가 → 409 INVALID_REQUEST(UNIQUE 위반)."""
        await api_client.post("/holdings", json={"ticker": "005930", "chat_id": CHAT_ID})
        resp2 = await api_client.post(
            "/holdings", json={"ticker": "005930", "chat_id": CHAT_ID}
        )
        assert resp2.status_code == 409
        assert resp2.json()["error_code"] == "INVALID_REQUEST"


class TestGetHoldings:
    """GET /holdings — TEST_SPEC §1.2"""

    @pytest.mark.asyncio
    async def test_api_003_empty_list(self, api_client, db_pool):
        """API-003: 보유 0개 → 200 + 빈 items."""
        resp = await api_client.get("/holdings", params={"chat_id": CHAT_ID})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"items": [], "total": 0}

    @pytest.mark.asyncio
    async def test_api_004_multiple_holdings(self, api_client, db_pool):
        """API-004: 보유 N개 → 200 + items 배열."""
        await HoldingFactory.create(db_pool, ticker="005930", chat_id=CHAT_ID)
        await HoldingFactory.create(db_pool, ticker="000660", chat_id=CHAT_ID)
        resp = await api_client.get("/holdings", params={"chat_id": CHAT_ID})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        tickers = {item["ticker"] for item in body["items"]}
        assert tickers == {"005930", "000660"}


class TestDeleteHolding:
    """DELETE /holdings/{ticker} — TEST_SPEC §1.3"""

    @pytest.mark.asyncio
    async def test_api_005_delete_existing(self, api_client, db_pool):
        """API-005: 존재하는 종목 제거 → 204."""
        await HoldingFactory.create(db_pool, ticker="005930", chat_id=CHAT_ID)
        resp = await api_client.delete("/holdings/005930", params={"chat_id": CHAT_ID})
        assert resp.status_code == 204

        # 실제로 삭제됐는지
        list_resp = await api_client.get("/holdings", params={"chat_id": CHAT_ID})
        assert list_resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_api_e005_delete_nonexistent(self, api_client, db_pool):
        """API-E005: 존재하지 않는 종목 제거 → 404 HOLDING_NOT_FOUND."""
        resp = await api_client.delete("/holdings/999999", params={"chat_id": CHAT_ID})
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "HOLDING_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_api_e006_delete_invalid_format(self, api_client, db_pool):
        """API-E006: ticker 형식 오류 → 400 INVALID_REQUEST."""
        resp = await api_client.delete("/holdings/abc", params={"chat_id": CHAT_ID})
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_REQUEST"


class TestUserScopedHoldings:
    """multi-user 시나리오 — 사용자별 분리 검증."""

    @pytest.mark.asyncio
    async def test_user_a_cannot_see_user_b_holdings(self, api_client, db_pool):
        """user A의 holdings GET이 user B 데이터를 안 보여준다."""
        chat_a, chat_b = CHAT_ID, 22222222
        await UserFactory.create(db_pool, chat_id=chat_b, status="active")
        await HoldingFactory.create(db_pool, ticker="005930", chat_id=chat_a)
        await HoldingFactory.create(db_pool, ticker="000660", chat_id=chat_b)

        a_resp = await api_client.get("/holdings", params={"chat_id": chat_a})
        b_resp = await api_client.get("/holdings", params={"chat_id": chat_b})
        assert {it["ticker"] for it in a_resp.json()["items"]} == {"005930"}
        assert {it["ticker"] for it in b_resp.json()["items"]} == {"000660"}

    @pytest.mark.asyncio
    async def test_pending_user_blocked(self, api_client, db_pool):
        """status='pending'인 사용자는 holdings 조작 불가 → 403 FORBIDDEN."""
        pending_chat = 33333333
        await UserFactory.create(db_pool, chat_id=pending_chat, status="pending")
        resp = await api_client.post(
            "/holdings", json={"ticker": "005930", "chat_id": pending_chat}
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_unregistered_chat_id_blocked(self, api_client, db_pool):
        """등록되지 않은 chat_id → 404 USER_NOT_FOUND."""
        resp = await api_client.post(
            "/holdings", json={"ticker": "005930", "chat_id": 99999999}
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"
