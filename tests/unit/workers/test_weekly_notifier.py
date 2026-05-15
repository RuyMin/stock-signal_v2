"""weekly_processor 메시지 포맷터 + fan-out 분기 단위 테스트.

LLM/Kafka는 mock. format_weekly_message는 순수 함수, notify_weekly는 Bot mock.
"""
from __future__ import annotations

import os
import sys

import pytest

_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "telegram_notifier")
)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)


def _sample_event(per_user_etfs: dict | None = None) -> dict:
    return {
        "job_id": "00000000-0000-0000-0000-0000000000aa",
        "week_start": "2026-05-04",
        "week_end": "2026-05-11",
        "macro": {
            "indicators": [
                {"name": "us10y", "start": 4.50, "end": 4.42,
                 "delta_abs": -0.08, "delta_pct": -1.8},
                {"name": "dxy", "start": 105.2, "end": 104.6,
                 "delta_abs": -0.6, "delta_pct": -0.57},
                {"name": "wti", "start": 80.0, "end": 80.0,
                 "delta_abs": 0.0, "delta_pct": 0.0},
                {"name": "sp500", "start": 5000.0, "end": 5105.0,
                 "delta_abs": 105.0, "delta_pct": 2.1},
                {"name": "gold", "start": None, "end": None,
                 "delta_abs": None, "delta_pct": None},
            ],
        },
        "per_user_etfs": per_user_etfs or {},
    }


class TestFormatWeeklyMessage:
    def test_includes_header_and_indicators(self):
        from weekly_processor import format_weekly_message
        evt = _sample_event()
        etfs = [{
            "ticker": "379800",
            "name": "KODEX 미국S&P500",
            "verdict": "favorable",
            "reason": "S&P500 주간 +2.1% + DXY 약세",
        }]
        msg = format_weekly_message(evt, etfs)
        assert "📅 주간 매크로 리포트" in msg
        assert "2026-05-04" in msg
        assert "2026-05-11" in msg
        assert "📊 매크로 5지표" in msg
        assert "미 10년물" in msg  # 한글 라벨
        assert "S&P 500" in msg
        # 데이터 부족 항목은 명시
        assert "금: 데이터 부족" in msg
        # 사용자 ETF 평가 섹션
        assert "📌 내 ETF 평가" in msg
        assert "🟢" in msg  # favorable
        assert "KODEX 미국S&P500" in msg
        assert "379800" in msg
        assert "S&P500 주간 +2.1%" in msg

    def test_verdict_emoji_mapping(self):
        from weekly_processor import format_weekly_message
        etfs = [
            {"ticker": "1", "name": "A", "verdict": "favorable", "reason": "..."},
            {"ticker": "2", "name": "B", "verdict": "caution", "reason": "..."},
            {"ticker": "3", "name": "C", "verdict": "unfavorable", "reason": "..."},
        ]
        msg = format_weekly_message(_sample_event(), etfs)
        assert "🟢" in msg
        assert "🟡" in msg
        assert "🔴" in msg


class _MockBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.fail_for: set[int] = set()

    async def send_message(self, chat_id, text, parse_mode=None, disable_web_page_preview=None):
        if int(chat_id) in self.fail_for:
            raise RuntimeError("simulated failure")
        self.sent.append({"chat_id": int(chat_id), "text": text})


class TestNotifyWeekly:
    @pytest.mark.asyncio
    async def test_no_etf_holders_silent_skip(self):
        """per_user_etfs 비어있으면 메시지 송신 안 함, recipient_count=0."""
        from weekly_processor import notify_weekly
        bot = _MockBot()
        result = await notify_weekly(bot, _sample_event(per_user_etfs={}))
        assert result["recipient_count"] == 0
        assert result["sent_count"] == 0
        assert len(bot.sent) == 0

    @pytest.mark.asyncio
    async def test_fans_out_to_each_chat_id(self):
        """per_user_etfs의 각 chat_id에 메시지 발송."""
        from weekly_processor import notify_weekly
        bot = _MockBot()
        evt = _sample_event(per_user_etfs={
            "11111": [{"ticker": "379800", "name": "KODEX S&P", "verdict": "favorable", "reason": "..."}],
            "22222": [{"ticker": "069500", "name": "KODEX 200", "verdict": "caution", "reason": "..."}],
        })
        result = await notify_weekly(bot, evt)
        assert result["recipient_count"] == 2
        assert result["sent_count"] == 2
        assert {s["chat_id"] for s in bot.sent} == {11111, 22222}

    @pytest.mark.asyncio
    async def test_per_user_failure_isolated(self):
        """한 사용자 송신 실패가 다음 사용자에 영향 없음."""
        from weekly_processor import notify_weekly
        bot = _MockBot()
        bot.fail_for = {11111}
        evt = _sample_event(per_user_etfs={
            "11111": [{"ticker": "379800", "name": "A", "verdict": "favorable", "reason": "..."}],
            "22222": [{"ticker": "069500", "name": "B", "verdict": "favorable", "reason": "..."}],
        })
        result = await notify_weekly(bot, evt)
        assert result["sent_count"] == 1
        assert result["failed_count"] == 1
        # 22222는 송신됨
        assert {s["chat_id"] for s in bot.sent} == {22222}

    @pytest.mark.asyncio
    async def test_empty_etf_list_for_chat_id_skipped(self):
        """per_user_etfs의 값이 빈 리스트면 그 사용자에게 송신 안 함."""
        from weekly_processor import notify_weekly
        bot = _MockBot()
        evt = _sample_event(per_user_etfs={"11111": []})
        result = await notify_weekly(bot, evt)
        assert result["sent_count"] == 0
        assert len(bot.sent) == 0
