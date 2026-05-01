"""worker-telegram-listener 단위 테스트 — multi-user (TEST_SPEC WRK-013~019, WRK-E010~013).

handlers를 직접 호출, BackendClient를 fake로 대체.
인증 로직은 backend `/users/by-chat-id` 응답에 의존하므로 fake가 user 응답 흉내냄.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

_LISTENER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "telegram_listener")
)
if _LISTENER_ROOT not in sys.path:
    sys.path.insert(0, _LISTENER_ROOT)

ACTIVE_CHAT_ID = 12345
PENDING_CHAT_ID = 22222
UNKNOWN_CHAT_ID = 99999
ADMIN_CHAT_ID = 10000


# ─── Fakes ─────────────────────────────────────────────────────────


@dataclass
class _FakeChat:
    id: int
    sent: list[str] = field(default_factory=list)

    async def send_message(self, text: str, **kwargs):
        self.sent.append(text)


@dataclass
class _FakeUser:
    username: str | None = None


@dataclass
class _FakeUpdate:
    effective_chat: _FakeChat | None
    effective_user: _FakeUser | None = None


@dataclass
class _FakeContext:
    args: list[str] = field(default_factory=list)
    bot_data: dict[str, Any] = field(default_factory=dict)


class FakeBackend:
    """backend 호출을 흉내냄. 사전 정의된 user 상태 + holdings/recommendations 응답."""

    def __init__(self):
        # chat_id -> user 응답 또는 None(미등록)
        self.users: dict[int, dict] = {
            ACTIVE_CHAT_ID: {"chat_id": ACTIVE_CHAT_ID, "status": "active", "is_admin": False},
            PENDING_CHAT_ID: {"chat_id": PENDING_CHAT_ID, "status": "pending", "is_admin": False},
            ADMIN_CHAT_ID: {"chat_id": ADMIN_CHAT_ID, "status": "active", "is_admin": True},
        }
        self.calls: list[tuple[str, tuple, dict]] = []
        self.add_holding_response = (201, {"ticker": "005930", "name": "삼성전자"})
        self.remove_holding_response = (204, {})
        self.list_holdings_response = (200, {"items": [], "total": 0})
        self.recent_response = (200, {"items": [], "total": 0})
        self.by_date_response = (200, {"items": [], "total": 0})
        self.approve_response = (200, {"chat_id": 0, "status": "active"})

    async def register_user(self, chat_id, telegram_username=None):
        self.calls.append(("register_user", (chat_id,), {"telegram_username": telegram_username}))
        if chat_id in self.users:
            return 201, self.users[chat_id]
        new = {"chat_id": chat_id, "status": "pending", "is_admin": False,
               "telegram_username": telegram_username}
        self.users[chat_id] = new
        return 201, new

    async def get_user(self, chat_id):
        self.calls.append(("get_user", (chat_id,), {}))
        if chat_id in self.users:
            return 200, self.users[chat_id]
        return 404, {"error_code": "USER_NOT_FOUND"}

    async def approve_user(self, target_chat_id, approved_by_chat_id):
        self.calls.append(
            ("approve_user", (target_chat_id, approved_by_chat_id), {})
        )
        return self.approve_response

    async def list_users(self):
        self.calls.append(("list_users", (), {}))
        return 200, {"items": list(self.users.values()), "total": len(self.users)}

    async def add_holding(self, ticker, chat_id):
        self.calls.append(("add_holding", (ticker, chat_id), {}))
        return self.add_holding_response

    async def remove_holding(self, ticker, chat_id):
        self.calls.append(("remove_holding", (ticker, chat_id), {}))
        return self.remove_holding_response

    async def list_holdings(self, chat_id):
        self.calls.append(("list_holdings", (chat_id,), {}))
        return self.list_holdings_response

    async def get_recent_recommendations(self, limit=7):
        self.calls.append(("get_recent_recommendations", (), {"limit": limit}))
        return self.recent_response

    async def get_recommendations_by_date(self, date_str):
        self.calls.append(("get_recommendations_by_date", (date_str,), {}))
        return self.by_date_response


# ─── Helpers ───────────────────────────────────────────────────────


def _make_update(chat_id: int = ACTIVE_CHAT_ID, username: str | None = None) -> _FakeUpdate:
    return _FakeUpdate(
        effective_chat=_FakeChat(id=chat_id),
        effective_user=_FakeUser(username=username),
    )


def _make_context(backend: FakeBackend, args: list[str] | None = None) -> _FakeContext:
    return _FakeContext(args=args or [], bot_data={"backend": backend})


# ─── 정상 케이스 ────────────────────────────────────────────────────


class TestStart:
    @pytest.mark.asyncio
    async def test_start_active_user_welcomed(self):
        """이미 active인 사용자가 /start → 환영 메시지."""
        from handlers import start
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await start(update, _make_context(backend))
        assert any("환영" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_wrk_013_start_new_user_pending(self):
        """WRK-013: 신규 chat_id로 /start → register + pending 안내."""
        from handlers import start
        update = _make_update(UNKNOWN_CHAT_ID)
        backend = FakeBackend()
        await start(update, _make_context(backend))
        # register_user 호출됨
        register_calls = [c for c in backend.calls if c[0] == "register_user"]
        assert len(register_calls) == 1
        assert any("승인" in t or "stock-signal" in t for t in update.effective_chat.sent)


class TestActiveUserCommands:
    @pytest.mark.asyncio
    async def test_wrk_014_help(self):
        """WRK-014: /help → 모든 명령어 설명."""
        from handlers import help_cmd
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await help_cmd(update, _make_context(backend))
        text = update.effective_chat.sent[0]
        for cmd in ["/add", "/remove", "/list", "/recent"]:
            assert cmd in text

    @pytest.mark.asyncio
    async def test_wrk_015_add(self):
        """WRK-015: /add 005930 → POST /holdings (chat_id 포함)."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["005930"]))
        # add_holding은 (ticker, chat_id) 시그니처
        add_calls = [c for c in backend.calls if c[0] == "add_holding"]
        assert add_calls[0] == ("add_holding", ("005930", ACTIVE_CHAT_ID), {})
        assert any("삼성전자" in t and "005930" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_wrk_016_remove(self):
        """WRK-016: /remove 005930 → DELETE /holdings/005930?chat_id=..."""
        from handlers import remove
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await remove(update, _make_context(backend, args=["005930"]))
        remove_calls = [c for c in backend.calls if c[0] == "remove_holding"]
        assert remove_calls[0] == ("remove_holding", ("005930", ACTIVE_CHAT_ID), {})
        assert any("제거됨" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_wrk_017_list(self):
        """WRK-017: /list → GET /holdings?chat_id=..."""
        from handlers import list_cmd
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.list_holdings_response = (
            200,
            {"items": [{"ticker": "005930", "name": "삼성전자"}], "total": 1},
        )
        await list_cmd(update, _make_context(backend))
        list_calls = [c for c in backend.calls if c[0] == "list_holdings"]
        assert list_calls[0] == ("list_holdings", (ACTIVE_CHAT_ID,), {})
        assert any("삼성전자" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_wrk_018_recent_default(self):
        """WRK-018: /recent → 시장 공통 추천 (chat_id 무관)."""
        from handlers import recent
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.recent_response = (
            200,
            {
                "items": [
                    {"date": "2026-04-28", "ticker": "005930", "name": "삼성전자",
                     "recommendation_type": "buy_hedge", "score": 85},
                ],
                "total": 1,
            },
        )
        await recent(update, _make_context(backend))
        recent_calls = [c for c in backend.calls if c[0] == "get_recent_recommendations"]
        assert recent_calls[0] == ("get_recent_recommendations", (), {"limit": 7})

    @pytest.mark.asyncio
    async def test_wrk_019_recent_with_date(self):
        """WRK-019: /recent 2026-04-25 → GET /recommendations?date=..."""
        from handlers import recent
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await recent(update, _make_context(backend, args=["2026-04-25"]))
        date_calls = [c for c in backend.calls if c[0] == "get_recommendations_by_date"]
        assert date_calls[0] == ("get_recommendations_by_date", ("2026-04-25",), {})


class TestAdminApprove:
    @pytest.mark.asyncio
    async def test_admin_approves_pending(self):
        """admin이 /approve <chat_id> → backend approve_user 호출 + 성공 메시지."""
        from handlers import approve
        update = _make_update(ADMIN_CHAT_ID)
        backend = FakeBackend()
        await approve(update, _make_context(backend, args=[str(PENDING_CHAT_ID)]))
        approve_calls = [c for c in backend.calls if c[0] == "approve_user"]
        assert approve_calls[0] == ("approve_user", (PENDING_CHAT_ID, ADMIN_CHAT_ID), {})
        assert any("승인 완료" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_non_admin_blocked_by_backend(self):
        """non-admin이 /approve → backend가 403 반환 → 권한 없음 안내."""
        from handlers import approve
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.approve_response = (403, {"error_code": "FORBIDDEN"})
        await approve(update, _make_context(backend, args=[str(PENDING_CHAT_ID)]))
        assert any("admin 권한이 없습니다" in t for t in update.effective_chat.sent)


# ─── 에러 케이스 ───────────────────────────────────────────────────


class TestUnauthorized:
    @pytest.mark.asyncio
    async def test_wrk_e010_unknown_chat_id_blocked(self):
        """WRK-E010: 미등록 chat_id로 /list → 등록 안내."""
        from handlers import list_cmd
        update = _make_update(UNKNOWN_CHAT_ID)
        backend = FakeBackend()
        await list_cmd(update, _make_context(backend))
        assert any("/start" in t for t in update.effective_chat.sent)
        # backend.list_holdings 호출 안 됨
        assert not any(c[0] == "list_holdings" for c in backend.calls)

    @pytest.mark.asyncio
    async def test_pending_user_blocked(self):
        """pending 상태 사용자가 명령 → 승인 대기 안내."""
        from handlers import add
        update = _make_update(PENDING_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["005930"]))
        assert any("pending" in t for t in update.effective_chat.sent)


class TestActiveUserErrors:
    @pytest.mark.asyncio
    async def test_wrk_e011_add_invalid_format(self):
        """WRK-E011: /add abc → "6자리 숫자입니다" 에러 메시지."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["abc"]))
        assert any("6자리 숫자" in t for t in update.effective_chat.sent)
        # add_holding 호출 안 됨
        assert not any(c[0] == "add_holding" for c in backend.calls)

    @pytest.mark.asyncio
    async def test_wrk_e012_backend_5xx(self):
        """WRK-E012: Backend 5xx → "잠시 후 다시 시도해주세요"."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.add_holding_response = (503, {})
        await add(update, _make_context(backend, args=["005930"]))
        assert any("잠시 후 다시 시도" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_wrk_e013_unknown_command(self):
        """WRK-E013: 알 수 없는 명령어 → "/help를 참조하세요"."""
        from handlers import unknown
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await unknown(update, _make_context(backend))
        assert any("/help" in t for t in update.effective_chat.sent)
