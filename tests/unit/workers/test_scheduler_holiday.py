"""scheduler 휴장일 처리 + 2-cron 단위 테스트.

scheduler.main의 is_market_open / previous_business_day와
trigger_intraday / trigger_premarket(휴장일 스킵 포함)을 직접 검증.
freezegun으로 시각 고정 + Kafka producer는 mock.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

_KST = ZoneInfo("Asia/Seoul")


def _kst(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """KST 시각을 timezone-aware datetime으로 반환 — freezegun에 그대로 전달."""
    return datetime(year, month, day, hour, minute, tzinfo=_KST)

_SCHEDULER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scheduler")
)
if _SCHEDULER_ROOT not in sys.path:
    sys.path.insert(0, _SCHEDULER_ROOT)


class TestIsMarketOpen:
    """주말 + 한국 공휴일 → False, 평일 + 비공휴일 → True."""

    def test_saturday_closed(self):
        from main import is_market_open
        assert is_market_open(date(2026, 5, 2)) is False  # 토

    def test_sunday_closed(self):
        from main import is_market_open
        assert is_market_open(date(2026, 5, 3)) is False  # 일

    def test_weekday_open(self):
        from main import is_market_open
        # 2026-04-30 목, 비공휴일
        assert is_market_open(date(2026, 4, 30)) is True

    def test_korean_national_holiday_closed(self):
        """2026-05-05 화요일 어린이날 — KR 공휴일."""
        from main import is_market_open
        assert is_market_open(date(2026, 5, 5)) is False


class TestPreviousBusinessDay:
    """previous_business_day는 주말/공휴일을 건너뛰며 직전 거래일 반환."""

    def test_monday_returns_previous_friday(self):
        from main import previous_business_day
        # 2026-05-04 월요일 → 직전 금요일 2026-05-01
        assert previous_business_day(date(2026, 5, 4)) == date(2026, 5, 1)

    def test_skips_korean_holiday(self):
        from main import previous_business_day
        # 2026-05-06 수요일 → 5/5(어린이날 공휴일) 스킵 → 5/4 월요일
        assert previous_business_day(date(2026, 5, 6)) == date(2026, 5, 4)


class TestHolidaysBetween:
    """holidays_between은 (start, end) 사이 휴장일 메타 반환."""

    def test_consecutive_business_days_no_gap(self):
        from main import holidays_between
        # 4/29 수 → 4/30 목 (연속 평일, 갭 없음)
        assert holidays_between(date(2026, 4, 29), date(2026, 4, 30)) == []

    def test_weekend_gap(self):
        from main import holidays_between
        # 5/1 금 → 5/4 월 사이 = 5/2 토, 5/3 일
        result = holidays_between(date(2026, 5, 1), date(2026, 5, 4))
        dates = [r["date"] for r in result]
        assert dates == ["2026-05-02", "2026-05-03"]
        assert all("주말" in r["reason"] for r in result)

    def test_korean_holiday_gap(self):
        from main import holidays_between
        # 5/4 월 → 5/6 수 사이 = 5/5 어린이날
        result = holidays_between(date(2026, 5, 4), date(2026, 5, 6))
        assert len(result) == 1
        assert result[0]["date"] == "2026-05-05"
        assert "어린이날" in result[0]["reason"]


def _patch_producer(monkeypatch):
    import main as sched
    sent: list[tuple[str, dict]] = []

    class _FakeProducer:
        async def send_and_wait(self, topic, raw):
            decoded = json.loads(raw.decode("utf-8")) if isinstance(raw, bytes) else raw
            sent.append((topic, decoded))

    monkeypatch.setattr(sched, "producer", _FakeProducer())
    return sent


class TestTriggerIntraday:
    """KST 16:30 트리거 — D일 거래일 검사 + mode='intraday' 발행."""

    @pytest.mark.asyncio
    async def test_holiday_skip_no_publish(self, monkeypatch):
        from freezegun import freeze_time
        import main as sched
        sent = _patch_producer(monkeypatch)

        # 토요일 KST 16:30
        with freeze_time(_kst(2026, 5, 2, 16, 30)):
            await sched.trigger_intraday()

        assert sent == []

    @pytest.mark.asyncio
    async def test_weekday_publishes_intraday(self, monkeypatch):
        from freezegun import freeze_time
        import main as sched
        sent = _patch_producer(monkeypatch)

        # 2026-04-30 목요일 KST 16:30
        with freeze_time(_kst(2026, 4, 30, 16, 30)):
            await sched.trigger_intraday()

        assert len(sent) == 1
        topic, payload = sent[0]
        assert topic == "stock.data.requested"
        assert payload["mode"] == "intraday"
        assert payload["target_date"] == "2026-04-30"
        assert "target_trading_date" not in payload  # intraday는 미설정


class TestTriggerPremarket:
    """KST 06:30 트리거 — D+1(오늘) 거래일 검사 + signal_date는 직전 거래일."""

    @pytest.mark.asyncio
    async def test_weekday_publishes_premarket(self, monkeypatch):
        from freezegun import freeze_time
        import main as sched
        sent = _patch_producer(monkeypatch)

        # 2026-04-30 목요일 KST 06:30
        with freeze_time(_kst(2026, 4, 30, 6, 30)):
            await sched.trigger_premarket()

        assert len(sent) == 1
        topic, payload = sent[0]
        assert topic == "stock.data.requested"
        assert payload["mode"] == "premarket"
        # signal_date = 직전 거래일(수요일 4/29)
        assert payload["target_date"] == "2026-04-29"
        # target_trading_date = 오늘(목요일 4/30)
        assert payload["target_trading_date"] == "2026-04-30"
        # 평일 연속 → 휴장일 갭 없음
        assert payload["holiday_gap_days"] == 0
        assert payload["holidays_in_gap"] == []

    @pytest.mark.asyncio
    async def test_premarket_after_holiday_includes_gap_meta(self, monkeypatch):
        """5/6 수 새벽 트리거 → signal=5/4 월. 갭에 5/5(어린이날) 포함."""
        from freezegun import freeze_time
        import main as sched
        sent = _patch_producer(monkeypatch)

        with freeze_time(_kst(2026, 5, 6, 6, 30)):
            await sched.trigger_premarket()

        assert len(sent) == 1
        _, payload = sent[0]
        assert payload["target_date"] == "2026-05-04"
        assert payload["target_trading_date"] == "2026-05-06"
        assert payload["holiday_gap_days"] == 1
        assert len(payload["holidays_in_gap"]) == 1
        assert payload["holidays_in_gap"][0]["date"] == "2026-05-05"
        assert "어린이날" in payload["holidays_in_gap"][0]["reason"]

    @pytest.mark.asyncio
    async def test_today_holiday_skip(self, monkeypatch):
        """오늘이 한국 공휴일/주말이면 premarket 트리거 스킵."""
        from freezegun import freeze_time
        import main as sched
        sent = _patch_producer(monkeypatch)

        # 2026-05-05 화 어린이날 KST 06:30
        with freeze_time(_kst(2026, 5, 5, 6, 30)):
            await sched.trigger_premarket()

        assert sent == []

    @pytest.mark.asyncio
    async def test_monday_signal_date_is_friday(self, monkeypatch):
        """월요일 새벽 트리거 시 signal_date는 직전 금요일."""
        from freezegun import freeze_time
        import main as sched
        sent = _patch_producer(monkeypatch)

        # 2026-05-04 월요일 KST 06:30
        with freeze_time(_kst(2026, 5, 4, 6, 30)):
            await sched.trigger_premarket()

        assert len(sent) == 1
        _, payload = sent[0]
        assert payload["target_date"] == "2026-05-01"  # 직전 금요일
        assert payload["target_trading_date"] == "2026-05-04"
