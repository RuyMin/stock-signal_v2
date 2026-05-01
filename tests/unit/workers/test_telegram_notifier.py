"""worker-telegram-notifier 단위 테스트 (multi-user fan-out).

multi-user 전환(2026-04-30): notify() 시그니처에서 chat_id 제거 — DB users 풀에서 자동 fan-out.
formatter는 그대로(메시지 포맷 자체는 user 무관). exit_alert 사용자별 필터는 processor._filter_for_user.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from tests.factories import HoldingFactory, RecommendationFactory, UserFactory

_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "telegram_notifier")
)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)


# ─── Fake Bot ──────────────────────────────────────────────────────


@dataclass
class _FakeMessage:
    message_id: int


class FakeBot:
    """모든 사용자 송신 성공. message_id 자동 증가."""

    def __init__(self, base_id: int = 100):
        self.sent: list[dict[str, Any]] = []
        self._next_id = base_id

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": str(chat_id), "text": text, **kwargs})
        mid = self._next_id
        self._next_id += 1
        return _FakeMessage(message_id=mid)


class FailingBot:
    """특정 chat_id에 대해 TelegramError 발생, 나머지 정상 송신 — 격리 검증용."""

    def __init__(self, fail_chat_id: int):
        self._fail_chat_id = str(fail_chat_id)
        self.sent: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text, **kwargs):
        from telegram.error import TelegramError
        if str(chat_id) == self._fail_chat_id:
            self.failed.append({"chat_id": str(chat_id)})
            raise TelegramError("simulated send failure")
        self.sent.append({"chat_id": str(chat_id), "text": text, **kwargs})
        return _FakeMessage(message_id=999)


class TokenInvalidBot:
    async def send_message(self, *args, **kwargs):
        from telegram.error import TelegramError
        raise TelegramError("Unauthorized: bot token invalid")


class RateLimitBot:
    async def send_message(self, *args, **kwargs):
        from telegram.error import RetryAfter
        raise RetryAfter(retry_after=1)


# ─── 정상 케이스 (fan-out) ──────────────────────────────────────────


class TestFanOut:
    @pytest.mark.asyncio
    async def test_wrk_007_send_to_multiple_users(self, db_pool):
        """WRK-007 (multi-user): 활성 사용자 모두에게 송신."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        await UserFactory.create(db_pool, chat_id=22222222, status="active")
        # pending 사용자는 송신 대상 아님
        await UserFactory.create(db_pool, chat_id=33333333, status="pending")

        target = date(2026, 4, 29)
        await RecommendationFactory.create(db_pool, date(2026, 4, 28), target, ticker="005930")

        from processor import notify
        bot = FakeBot()
        result = await notify(db_pool, bot, target)
        assert result["user_count"] == 2  # active만
        assert result["sent_count"] == 2
        assert {s["chat_id"] for s in bot.sent} == {"11111111", "22222222"}

    @pytest.mark.asyncio
    async def test_wrk_008_header_includes_target_trading_date(self, db_pool):
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        target = date(2026, 4, 29)
        await RecommendationFactory.create(db_pool, date(2026, 4, 28), target, ticker="005930")

        from processor import notify
        bot = FakeBot()
        await notify(db_pool, bot, target)
        text = bot.sent[0]["text"]
        assert "2026-04-29" in text
        assert "다음 거래일" in text

    @pytest.mark.asyncio
    async def test_wrk_009_buy_hedge_estimated_avg_price(self, db_pool):
        from formatter import RecItem, format_message
        item = RecItem(
            ticker="005930", name="삼성전자",
            recommendation_type="buy_hedge", score=85,
            reason_supply="기관 5일 매수", reason_news="실적 호재",
            reason_macro="DXY 하락", estimated_avg_price=Decimal("72000"),
        )
        text = format_message(date(2026, 4, 28), date(2026, 4, 29), [item])
        assert "추정 매집가" in text
        assert "72,000" in text

    @pytest.mark.asyncio
    async def test_wrk_010_exit_alert_with_holdings_label(self, db_pool):
        from formatter import RecItem, format_message
        item = RecItem(
            ticker="005930", name="삼성전자",
            recommendation_type="exit_alert", score=40,
            reason_supply="기관 매도 전환", reason_news=None, reason_macro=None,
            estimated_avg_price=None,
        )
        text = format_message(date(2026, 4, 28), date(2026, 4, 29), [item])
        assert "🔴 탈출 경보 (1종목 — 보유)" in text
        assert "익절/손절" in text

    @pytest.mark.asyncio
    async def test_wrk_011_zero_recommendations_message(self, db_pool):
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        from processor import notify
        bot = FakeBot()
        result = await notify(db_pool, bot, date(2026, 4, 29))
        assert result["sent_count"] == 1
        assert "조건 충족 종목이 없습니다" in bot.sent[0]["text"]

    @pytest.mark.asyncio
    async def test_wrk_012_disclaimer_present(self, db_pool):
        from formatter import format_message
        text = format_message(date(2026, 4, 28), date(2026, 4, 29), [])
        assert "최종 판단은 본인이 직접 하세요" in text

    @pytest.mark.asyncio
    async def test_no_active_users_returns_zero_counts(self, db_pool):
        # active 사용자가 없으면 송신 없음
        await UserFactory.create(db_pool, chat_id=33333333, status="pending")
        from processor import notify
        bot = FakeBot()
        result = await notify(db_pool, bot, date(2026, 4, 29))
        assert result == {"sent_count": 0, "failed_count": 0, "user_count": 0}


# ─── 사용자별 exit_alert 필터 ──────────────────────────────────────


class TestUserSpecificExitAlert:
    @pytest.mark.asyncio
    async def test_exit_alert_excluded_for_non_holder(self, db_pool):
        """exit_alert 종목은 보유 사용자에게만 포함, 미보유 사용자는 메시지에서 제외."""
        holder = await UserFactory.create(db_pool, chat_id=11111111, status="active")
        non_holder = await UserFactory.create(db_pool, chat_id=22222222, status="active")
        await HoldingFactory.create(
            db_pool, ticker="005930", user_id=holder["id"], chat_id=11111111
        )
        target = date(2026, 4, 29)
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), target,
            ticker="005930", recommendation_type="exit_alert", score=30,
        )

        from processor import notify
        bot = FakeBot()
        await notify(db_pool, bot, target)

        msgs = {s["chat_id"]: s["text"] for s in bot.sent}
        # holder는 exit_alert 메시지 받음
        assert "탈출 경보" in msgs["11111111"]
        # non-holder는 같은 추천이 메시지에서 제외 → "조건 충족 종목 없음"
        assert "조건 충족" in msgs["22222222"]

    @pytest.mark.asyncio
    async def test_buy_hedge_shared_across_users(self, db_pool):
        """buy_hedge / watch는 모든 사용자에게 공통 송신 (시장 공통)."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        await UserFactory.create(db_pool, chat_id=22222222, status="active")
        target = date(2026, 4, 29)
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), target,
            ticker="005930", recommendation_type="buy_hedge", score=85,
        )

        from processor import notify
        bot = FakeBot()
        await notify(db_pool, bot, target)
        assert len(bot.sent) == 2
        assert all("매수 헬지" in s["text"] for s in bot.sent)


# ─── 격리 / 에러 케이스 ───────────────────────────────────────────


class TestNameFallback:
    """recommendations.name이 NULL이어도 holdings에 알려진 name으로 메시지 보강."""

    @pytest.mark.asyncio
    async def test_falls_back_to_holdings_name(self, db_pool):
        holder = await UserFactory.create(db_pool, chat_id=11111111, status="active")
        # 다른 사용자가 같은 종목을 등록해두기만 해도 fallback 가능 (시장 합집합)
        other = await UserFactory.create(db_pool, chat_id=22222222, status="active")
        await HoldingFactory.create(
            db_pool, ticker="003690", name="코리안리",
            user_id=other["id"], chat_id=22222222,
        )

        target = date(2026, 4, 29)
        # recommendations.name = NULL (LLM 응답 누락 시뮬)
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), target,
            ticker="003690", recommendation_type="buy_hedge", score=80,
        )
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE recommendations SET name = NULL WHERE ticker = '003690'"
            )

        from processor import notify
        bot = FakeBot()
        await notify(db_pool, bot, target)

        # 11111111 사용자에게 메시지가 가고, 거기에 "코리안리(003690)" 포함되어야 함
        text_for_holder = next(s["text"] for s in bot.sent if s["chat_id"] == "11111111")
        assert "코리안리(003690)" in text_for_holder

    @pytest.mark.asyncio
    async def test_recommendation_name_takes_priority(self, db_pool):
        """recommendations.name이 있으면 holdings의 다른 name이 있어도 무시."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        await HoldingFactory.create(
            db_pool, ticker="005930", name="삼성(이전명)", chat_id=11111111
        )

        target = date(2026, 4, 29)
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), target,
            ticker="005930", name="삼성전자",
            recommendation_type="buy_hedge", score=80,
        )

        from processor import notify
        bot = FakeBot()
        await notify(db_pool, bot, target)
        text = bot.sent[0]["text"]
        # recommendations.name이 우선
        assert "삼성전자(005930)" in text
        assert "삼성(이전명)" not in text

    @pytest.mark.asyncio
    async def test_no_name_anywhere_falls_back_to_ticker(self, db_pool):
        """recommendations.name도 NULL이고 holdings에도 없으면 ticker만 표시."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        target = date(2026, 4, 29)
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), target,
            ticker="999999", recommendation_type="buy_hedge", score=80,
        )
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE recommendations SET name = NULL WHERE ticker = '999999'"
            )

        from processor import notify
        bot = FakeBot()
        await notify(db_pool, bot, target)
        text = bot.sent[0]["text"]
        assert "`999999`" in text  # 종목명 없이 ticker만


class TestIsolation:
    @pytest.mark.asyncio
    async def test_one_user_failure_does_not_block_others(self, db_pool):
        """한 사용자 송신 실패 → 다른 사용자는 그대로 송신."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        await UserFactory.create(db_pool, chat_id=22222222, status="active")
        await UserFactory.create(db_pool, chat_id=33333333, status="active")
        target = date(2026, 4, 29)
        await RecommendationFactory.create(
            db_pool, date(2026, 4, 28), target, ticker="005930"
        )

        from processor import notify
        bot = FailingBot(fail_chat_id=22222222)
        result = await notify(db_pool, bot, target)
        assert result["sent_count"] == 2
        assert result["failed_count"] == 1
        assert {s["chat_id"] for s in bot.sent} == {"11111111", "33333333"}


class TestErrors:
    @pytest.mark.asyncio
    async def test_wrk_e006_invalid_bot_token(self, db_pool):
        """모든 사용자에게 TelegramError → failed_count = user_count, raise 안 함 (격리)."""
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        from processor import notify
        bot = TokenInvalidBot()
        result = await notify(db_pool, bot, date(2026, 4, 29))
        assert result["sent_count"] == 0
        assert result["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_wrk_e008_rate_limit_propagates(self, db_pool):
        """RetryAfter는 raise — main.py가 sleep 후 재처리해야 함."""
        from telegram.error import RetryAfter
        await UserFactory.create(db_pool, chat_id=11111111, status="active")
        from processor import notify
        bot = RateLimitBot()
        with pytest.raises(RetryAfter):
            await notify(db_pool, bot, date(2026, 4, 29))

    @pytest.mark.asyncio
    async def test_wrk_e009_pg_query_failure(self, db_pool):
        """PG 풀 닫힘 → 예외 전파."""
        await db_pool.close()
        from processor import notify
        bot = FakeBot()
        with pytest.raises(Exception):
            await notify(db_pool, bot, date(2026, 4, 29))
