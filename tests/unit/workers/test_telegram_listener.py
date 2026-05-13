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
class _FakeMessage:
    text: str | None = None


@dataclass
class _FakeUpdate:
    effective_chat: _FakeChat | None
    effective_user: _FakeUser | None = None
    effective_message: _FakeMessage | None = None


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.fail_chat_ids: set[int] = set()

    async def send_message(self, chat_id: int, text: str, **kwargs):
        if chat_id in self.fail_chat_ids:
            raise RuntimeError(f"send_message failed for {chat_id}")
        self.sent.append((chat_id, text))


@dataclass
class _FakeContext:
    args: list[str] = field(default_factory=list)
    bot_data: dict[str, Any] = field(default_factory=dict)
    bot: _FakeBot = field(default_factory=_FakeBot)


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
        self.update_holding_response = (
            200,
            {"ticker": "005930", "name": "삼성전자", "avg_price": "75000.00"},
        )
        self.remove_holding_response = (204, {})
        self.list_holdings_response = (200, {"items": [], "total": 0})
        self.recent_response = (200, {"items": [], "total": 0})
        self.by_date_response = (200, {"items": [], "total": 0})
        self.approve_response = (200, {"chat_id": 0, "status": "active"})

    async def register_user(self, chat_id, telegram_username=None):
        self.calls.append(("register_user", (chat_id,), {"telegram_username": telegram_username}))
        if chat_id in self.users:
            return 200, self.users[chat_id]
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

    async def add_holding(self, ticker, chat_id, name=None, avg_price=None):
        self.calls.append(
            ("add_holding", (ticker, chat_id), {"name": name, "avg_price": avg_price})
        )
        return self.add_holding_response

    async def update_holding(
        self, ticker, chat_id, name=None, avg_price=None, clear_avg_price=False
    ):
        self.calls.append(
            (
                "update_holding",
                (ticker, chat_id),
                {"name": name, "avg_price": avg_price, "clear_avg_price": clear_avg_price},
            )
        )
        return self.update_holding_response

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

    async def get_recommendation_by_ticker(self, ticker, chat_id=None):
        self.calls.append(("get_recommendation_by_ticker", (ticker,), {"chat_id": chat_id}))
        return getattr(self, "by_ticker_response", (404, {"error_code": "RECOMMENDATION_NOT_FOUND"}))


# ─── Helpers ───────────────────────────────────────────────────────


def _make_update(
    chat_id: int = ACTIVE_CHAT_ID,
    username: str | None = None,
    text: str | None = None,
) -> _FakeUpdate:
    return _FakeUpdate(
        effective_chat=_FakeChat(id=chat_id),
        effective_user=_FakeUser(username=username),
        effective_message=_FakeMessage(text=text),
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

    @pytest.mark.asyncio
    async def test_new_pending_notifies_active_admin(self):
        """신규 pending 등록(201) → active admin chat에 알림 송신."""
        from handlers import start
        update = _make_update(UNKNOWN_CHAT_ID, username="newbie")
        backend = FakeBackend()
        ctx = _make_context(backend)
        await start(update, ctx)
        sent_to_admin = [s for s in ctx.bot.sent if s[0] == ADMIN_CHAT_ID]
        assert len(sent_to_admin) == 1
        msg = sent_to_admin[0][1]
        assert str(UNKNOWN_CHAT_ID) in msg
        assert "@newbie" in msg
        assert "/approve" in msg

    @pytest.mark.asyncio
    async def test_existing_active_user_no_admin_notify(self):
        """이미 active인 사용자가 /start → 200 응답, admin 알림 송신 없음."""
        from handlers import start
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        ctx = _make_context(backend)
        await start(update, ctx)
        assert ctx.bot.sent == []

    @pytest.mark.asyncio
    async def test_admin_notify_failure_isolated(self):
        """admin 송신 실패해도 신규 사용자에겐 정상 등록 안내가 송신됨."""
        from handlers import start
        update = _make_update(UNKNOWN_CHAT_ID)
        backend = FakeBackend()
        ctx = _make_context(backend)
        ctx.bot.fail_chat_ids = {ADMIN_CHAT_ID}
        await start(update, ctx)
        # 신규 사용자에겐 pending 안내 정상 송신
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
        """WRK-015: /add 005930 → POST /holdings (chat_id 포함, avg_price 미지정)."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["005930"]))
        add_calls = [c for c in backend.calls if c[0] == "add_holding"]
        assert add_calls[0] == (
            "add_holding", ("005930", ACTIVE_CHAT_ID),
            {"name": None, "avg_price": None},
        )
        assert any("삼성전자" in t and "005930" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_add_with_avg_price(self):
        """/add 005930 75000 → avg_price 전달 + 응답에 평단가 표시."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.add_holding_response = (
            201,
            {"ticker": "005930", "name": "삼성전자", "avg_price": "75000.00"},
        )
        await add(update, _make_context(backend, args=["005930", "75000"]))
        add_calls = [c for c in backend.calls if c[0] == "add_holding"]
        assert add_calls[0] == (
            "add_holding", ("005930", ACTIVE_CHAT_ID),
            {"name": None, "avg_price": "75000"},
        )
        assert any("75,000" in t and "평단가" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_add_with_name_only(self):
        """/add 005930 코리안리 → 비숫자 → 종목명으로 처리."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.add_holding_response = (
            201, {"ticker": "003690", "name": "코리안리", "avg_price": None},
        )
        await add(update, _make_context(backend, args=["003690", "코리안리"]))
        call = [c for c in backend.calls if c[0] == "add_holding"][0]
        assert call == (
            "add_holding", ("003690", ACTIVE_CHAT_ID),
            {"name": "코리안리", "avg_price": None},
        )
        assert any("코리안리" in t and "003690" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_add_with_name_and_price(self):
        """/add 003690 코리안리 5500 → name + avg_price 둘 다 전달."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.add_holding_response = (
            201, {"ticker": "003690", "name": "코리안리", "avg_price": "5500.00"},
        )
        await add(update, _make_context(backend, args=["003690", "코리안리", "5500"]))
        call = [c for c in backend.calls if c[0] == "add_holding"][0]
        assert call == (
            "add_holding", ("003690", ACTIVE_CHAT_ID),
            {"name": "코리안리", "avg_price": "5500"},
        )
        text = update.effective_chat.sent[0]
        assert "코리안리" in text and "5,500" in text and "평단가" in text

    @pytest.mark.asyncio
    async def test_add_three_args_invalid_price(self):
        """/add 003690 코리안리 abc → 3번째 인자 형식 오류 안내, backend 호출 없음."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["003690", "코리안리", "abc"]))
        assert any("3번째 인자" in t and "양의 숫자" in t for t in update.effective_chat.sent)
        assert not any(c[0] == "add_holding" for c in backend.calls)

    @pytest.mark.asyncio
    async def test_add_too_many_args(self):
        """/add에 인자 4개 이상 → 사용법 안내, backend 호출 없음."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["005930", "a", "100", "extra"]))
        assert any("사용법" in t for t in update.effective_chat.sent)
        assert not any(c[0] == "add_holding" for c in backend.calls)

    @pytest.mark.asyncio
    async def test_edit_set_avg_price(self):
        """/edit 005930 80000 → update_holding(avg_price='80000') 호출."""
        from handlers import edit
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.update_holding_response = (
            200,
            {"ticker": "005930", "name": "삼성전자", "avg_price": "80000.00"},
        )
        await edit(update, _make_context(backend, args=["005930", "80000"]))
        upd = [c for c in backend.calls if c[0] == "update_holding"]
        assert upd[0] == (
            "update_holding",
            ("005930", ACTIVE_CHAT_ID),
            {"name": None, "avg_price": "80000", "clear_avg_price": False},
        )
        assert any("80,000" in t and "갱신" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_edit_clear_avg_price(self):
        """/edit 005930 - → clear_avg_price=True 호출 + 제거 안내."""
        from handlers import edit
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.update_holding_response = (
            200,
            {"ticker": "005930", "name": "삼성전자", "avg_price": None},
        )
        await edit(update, _make_context(backend, args=["005930", "-"]))
        upd = [c for c in backend.calls if c[0] == "update_holding"]
        assert upd[0] == (
            "update_holding",
            ("005930", ACTIVE_CHAT_ID),
            {"name": None, "avg_price": None, "clear_avg_price": True},
        )
        assert any("제거됨" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_edit_set_name(self):
        """/edit 003690 코리안리 → 비숫자 → name 갱신."""
        from handlers import edit
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.update_holding_response = (
            200, {"ticker": "003690", "name": "코리안리", "avg_price": None},
        )
        await edit(update, _make_context(backend, args=["003690", "코리안리"]))
        upd = [c for c in backend.calls if c[0] == "update_holding"][0]
        assert upd == (
            "update_holding", ("003690", ACTIVE_CHAT_ID),
            {"name": "코리안리", "avg_price": None, "clear_avg_price": False},
        )
        assert any("종목명 갱신" in t and "코리안리" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_edit_unknown_ticker_returns_404(self):
        """/edit 999999 100 → backend 404 → 등록되지 않은 종목 안내."""
        from handlers import edit
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.update_holding_response = (404, {"error_code": "HOLDING_NOT_FOUND"})
        await edit(update, _make_context(backend, args=["999999", "100"]))
        assert any("등록되지 않은 종목" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_edit_missing_args(self):
        """/edit 005930 (인자 부족) → 사용법 안내."""
        from handlers import edit
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await edit(update, _make_context(backend, args=["005930"]))
        assert any("사용법" in t for t in update.effective_chat.sent)
        assert not any(c[0] == "update_holding" for c in backend.calls)

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
    async def test_list_shows_avg_price(self):
        """/list 응답에 avg_price 있으면 메시지에 평단가 표시."""
        from handlers import list_cmd
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.list_holdings_response = (
            200,
            {
                "items": [
                    {"ticker": "005930", "name": "삼성전자", "avg_price": "75000.00"},
                    {"ticker": "000660", "name": "SK하이닉스", "avg_price": None},
                ],
                "total": 2,
            },
        )
        await list_cmd(update, _make_context(backend))
        text = update.effective_chat.sent[0]
        assert "75,000" in text and "평단가" in text
        # 평단가 없는 종목은 평단가 줄 없이 그냥 표시
        assert "SK하이닉스" in text

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

    @pytest.mark.asyncio
    async def test_reason_buy_hedge_full_detail(self):
        """/reason 005930 → Detail 응답을 raw 데이터까지 포함해 자세히 표시."""
        from handlers import reason
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.by_ticker_response = (200, {
            "recommendation": {
                "id": 1, "date": "2026-05-06", "target_trading_date": "2026-05-07",
                "ticker": "005930", "name": "삼성전자보통주",
                "recommendation_type": "buy_hedge", "score": 100,
                "reason_supply": "강한 매수 흡수형",
                "reason_news": "AI 반도체 호재",
                "reason_macro": "S&P 사상 최고치",
                "estimated_avg_price": 72000,
            },
            "signals": [{
                "date": "2026-05-04",
                "agency_net_buy": 1756000, "foreign_net_buy": 4459000,
                "consecutive_buy_days": 2,
            }],
            "news": [
                {"date": "2026-05-06", "title": "AI 반도체 수요 증가", "url": "https://x"},
                {"date": "2026-05-04", "title": "씨티 목표주가 하향", "url": "https://y"},
            ],
            "macro": {
                "date": "2026-05-05",
                "us10y": 4.42, "dxy": 98.28, "wti": 100.48,
                "sp500": 7259.22, "gold": 4658.40,
            },
            "holding": {"avg_price": "75000.00", "name": "삼성전자보통주"},
            "institutional_avg": {"avg_price": "67500.00", "days": 2},
            "foreign_consecutive_buy_days": 3,
            "agency_consecutive_buy_days": 1,
        })
        await reason(update, _make_context(backend, args=["005930"]))
        # chat_id 전달됐는지
        call = [c for c in backend.calls if c[0] == "get_recommendation_by_ticker"][0]
        assert call[2]["chat_id"] == ACTIVE_CHAT_ID

        text = update.effective_chat.sent[0]
        # 헤더
        assert "삼성전자보통주" in text and "005930" in text
        assert "🟢" in text and "매수 헬지" in text and "100" in text
        # 시그널 raw
        assert "+4,459,000주" in text and "+1,756,000주" in text
        # 외인/기관 연속 매수일 분리 표시
        assert "외국인 3일" in text and "기관 1일" in text
        # 추정 평단가
        assert "외+기관 추정 평단가" in text and "67,500" in text
        # 뉴스 헤드라인
        assert "AI 반도체 수요 증가" in text and "씨티 목표주가 하향" in text
        # 매크로 5지표 모두
        assert "US10Y" in text and "4.42" in text
        assert "DXY" in text and "S&P500" in text
        # 보유 정보
        assert "💼 내 보유 정보" in text and "75,000원" in text
        # 추천 추정 매집가 (LLM 산출)
        assert "추천 추정 매집가" in text and "72,000원" in text

    @pytest.mark.asyncio
    async def test_reason_watch_minimal_data(self):
        """signals/news/macro 없는 경우 — 해당 섹션 자동 생략."""
        from handlers import reason
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.by_ticker_response = (200, {
            "recommendation": {
                "id": 2, "date": "2026-05-06", "target_trading_date": "2026-05-07",
                "ticker": "018880", "name": "한온시스템보통주",
                "recommendation_type": "watch", "score": 60,
                "reason_supply": "외인 순매수", "reason_news": None, "reason_macro": None,
                "estimated_avg_price": None,
            },
            "signals": [], "news": [], "macro": None,
            "holding": None, "institutional_avg": None,
        })
        await reason(update, _make_context(backend, args=["018880"]))
        text = update.effective_chat.sent[0]
        assert "🟡" in text and "관망" in text
        assert "📊 수급" in text  # reason_supply 있어 표시
        assert "📰 뉴스" not in text
        assert "🌐 매크로" not in text
        assert "💼 내 보유 정보" not in text
        assert "추천 추정 매집가" not in text
        assert "외+기관 추정 평단가" not in text

    @pytest.mark.asyncio
    async def test_reason_404_no_history(self):
        """추천 이력 없는 종목 → 안내 메시지."""
        from handlers import reason
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        backend.by_ticker_response = (404, {"error_code": "RECOMMENDATION_NOT_FOUND"})
        await reason(update, _make_context(backend, args=["999999"]))
        assert any("추천 이력이 없습니다" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_reason_invalid_format(self):
        """/reason abc → 형식 오류, backend 호출 없음."""
        from handlers import reason
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await reason(update, _make_context(backend, args=["abc"]))
        assert any("사용법" in t for t in update.effective_chat.sent)
        assert not any(c[0] == "get_recommendation_by_ticker" for c in backend.calls)


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


class TestAnnounce:
    @pytest.mark.asyncio
    async def test_admin_announce_fanout(self):
        """admin이 /announce → 자기 제외 active 사용자 전원에 공지 송신."""
        from handlers import announce
        update = _make_update(ADMIN_CHAT_ID, text="/announce 5/9 정기 점검 안내")
        backend = FakeBackend()
        ctx = _make_context(backend)
        await announce(update, ctx)

        # admin(ADMIN_CHAT_ID) 제외 + ACTIVE_CHAT_ID active만 (PENDING은 active 아님)
        sent_chats = [s[0] for s in ctx.bot.sent]
        assert ACTIVE_CHAT_ID in sent_chats
        assert ADMIN_CHAT_ID not in sent_chats  # 본인 제외
        assert PENDING_CHAT_ID not in sent_chats  # active 아님
        # 본문 + 헤더 포함
        assert "📢 공지" in ctx.bot.sent[0][1]
        assert "5/9 정기 점검 안내" in ctx.bot.sent[0][1]
        # admin에게 결과 요약
        assert any("공지 송신 완료" in t for t in update.effective_chat.sent)

    @pytest.mark.asyncio
    async def test_non_admin_denied(self):
        """일반 active 사용자가 /announce → 권한 거부 + bot 호출 없음."""
        from handlers import announce
        update = _make_update(ACTIVE_CHAT_ID, text="/announce hi")
        backend = FakeBackend()
        ctx = _make_context(backend)
        await announce(update, ctx)
        assert any("admin 권한이 필요합니다" in t for t in update.effective_chat.sent)
        assert ctx.bot.sent == []

    @pytest.mark.asyncio
    async def test_empty_message_usage_help(self):
        """/announce (메시지 없음) → 사용법 안내."""
        from handlers import announce
        update = _make_update(ADMIN_CHAT_ID, text="/announce   ")
        backend = FakeBackend()
        ctx = _make_context(backend)
        await announce(update, ctx)
        assert any("사용법" in t for t in update.effective_chat.sent)
        assert ctx.bot.sent == []

    @pytest.mark.asyncio
    async def test_send_failure_isolated(self):
        """일부 사용자 송신 실패 → 격리 + summary에 실패 카운트."""
        from handlers import announce
        update = _make_update(ADMIN_CHAT_ID, text="/announce 테스트")
        backend = FakeBackend()
        ctx = _make_context(backend)
        ctx.bot.fail_chat_ids = {ACTIVE_CHAT_ID}
        await announce(update, ctx)
        assert any("실패 1명" in t for t in update.effective_chat.sent)


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
        """WRK-E011: /add abc → 사용법 안내 메시지."""
        from handlers import add
        update = _make_update(ACTIVE_CHAT_ID)
        backend = FakeBackend()
        await add(update, _make_context(backend, args=["abc"]))
        assert any("사용법" in t and "/add 005930" in t for t in update.effective_chat.sent)
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
