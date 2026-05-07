"""POST/GET /users — multi-user 인증 및 admin 승인 흐름.

신규 (2026-04-30): 소규모 화이트리스트 multi-user 도입.
"""
import pytest

from tests.factories import UserFactory


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_new_user_pending(self, api_client, db_pool):
        """신규 chat_id로 register → 201, status=pending."""
        resp = await api_client.post(
            "/users/register",
            json={"chat_id": 11111111, "telegram_username": "testuser"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["chat_id"] == 11111111
        assert body["status"] == "pending"
        assert body["is_admin"] is False
        assert body["telegram_username"] == "testuser"

    @pytest.mark.asyncio
    async def test_register_existing_user_idempotent(self, api_client, db_pool):
        """이미 등록된 chat_id → 기존 user 그대로 반환 (200, 신규 생성 201과 구분)."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        resp = await api_client.post("/users/register", json={"chat_id": 11111111})
        assert resp.status_code == 200
        body = resp.json()
        assert body["chat_id"] == 11111111
        assert body["status"] == "active"  # 기존 active 그대로


class TestApprove:
    @pytest.mark.asyncio
    async def test_admin_approves_pending_user(self, api_client, db_pool):
        """admin이 pending 사용자 승인 → status=active."""
        admin = await UserFactory.create(
            db_pool, chat_id=10000000, status="active", is_admin=True
        )
        await UserFactory.create(db_pool, chat_id=20000000, status="pending")

        resp = await api_client.post(
            "/users/20000000/approve",
            json={"approved_by_chat_id": 10000000},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["chat_id"] == 20000000

    @pytest.mark.asyncio
    async def test_non_admin_cannot_approve(self, api_client, db_pool):
        """non-admin이 approve 시도 → 403 FORBIDDEN."""
        await UserFactory.create(db_pool, chat_id=10000000, status="active", is_admin=False)
        await UserFactory.create(db_pool, chat_id=20000000, status="pending")

        resp = await api_client.post(
            "/users/20000000/approve",
            json={"approved_by_chat_id": 10000000},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_approve_unknown_target(self, api_client, db_pool):
        """존재하지 않는 chat_id 승인 시도 → 404 USER_NOT_FOUND."""
        await UserFactory.create(
            db_pool, chat_id=10000000, status="active", is_admin=True
        )
        resp = await api_client.post(
            "/users/99999999/approve",
            json={"approved_by_chat_id": 10000000},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"


class TestGetByChatId:
    @pytest.mark.asyncio
    async def test_lookup_existing(self, api_client, db_pool):
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        resp = await api_client.get("/users/by-chat-id/11111111")
        assert resp.status_code == 200
        assert resp.json()["chat_id"] == 11111111

    @pytest.mark.asyncio
    async def test_lookup_unknown(self, api_client, db_pool):
        resp = await api_client.get("/users/by-chat-id/99999999")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"


class TestList:
    @pytest.mark.asyncio
    async def test_list_all(self, api_client, db_pool):
        await UserFactory.create(db_pool, chat_id=10000000, status="active", is_admin=True)
        await UserFactory.create(db_pool, chat_id=20000000, status="pending")
        resp = await api_client.get("/users")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
