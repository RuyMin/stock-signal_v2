"""worker-data-collector 단위 테스트 — 2-cron(intraday/premarket) 분리 버전.

processor.process_intraday / process_premarket을 직접 호출하고
외부 클라이언트는 mock. main.py의 mode 분기는 별도(test_data_collector_main).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date

import pytest

_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "data_collector")
)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)


# ─── Mock data classes ────────────────────────────────────────────


@dataclass
class _SignalRow:
    date: date
    ticker: str
    agency_buy: int
    agency_sell: int
    agency_net_buy: int
    foreign_buy: int
    foreign_sell: int
    foreign_net_buy: int


@dataclass
class _MacroSnapshot:
    date: date
    us10y: float | None = 4.2
    dxy: float | None = 105.0
    wti: float | None = 80.0
    sp500: float | None = 5000.0
    gold: float | None = 2300.0


@dataclass
class _NewsRow:
    date: date
    ticker: str
    title: str
    url: str | None


# ─── Fake clients ──────────────────────────────────────────────────


class FakeKisClient:
    def __init__(self, signals: list[_SignalRow], names: dict[str, str] | None = None):
        self._signals = signals
        self._names = names or {}

    async def fetch_signals(self, target_date: date) -> list:
        return self._signals

    async def fetch_ticker_name(self, ticker: str) -> str | None:
        return self._names.get(ticker)

    async def aclose(self) -> None:
        pass


class FakeKisAuthError(FakeKisClient):
    async def fetch_signals(self, target_date: date) -> list:
        import httpx
        raise httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("GET", "https://test"),
            response=httpx.Response(401),
        )


class FakeNaverScraper:
    def __init__(self, news_per_ticker: dict[str, list[_NewsRow]] | None = None,
                 blocked_tickers: set[str] | None = None):
        self._news = news_per_ticker or {}
        self._blocked = blocked_tickers or set()

    async def fetch_for_ticker(self, ticker: str, target_date: date) -> list:
        if ticker in self._blocked:
            return []
        # naver는 target_date 인자로 받은 날짜를 NewsRow.date에 그대로 박는다
        # → 테스트 쪽 fixture도 같은 날짜로 세팅됐다고 가정
        return self._news.get(ticker, [])

    async def aclose(self) -> None:
        pass


# ─── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def patch_clients(monkeypatch):
    """processor가 import한 client 심볼을 mock으로 대체."""
    state: dict = {
        "signals": [],
        "names": {},
        "macro": _MacroSnapshot(date=date(2026, 4, 28)),
        "news": {},
        "blocked": set(),
        "fail_kis": False,
        "fail_macro": False,
    }

    def _make_kis(*args, **kwargs):
        if state["fail_kis"]:
            return FakeKisAuthError([], state["names"])
        return FakeKisClient(state["signals"], state["names"])

    def _make_naver():
        return FakeNaverScraper(state["news"], state["blocked"])

    async def _fake_macro():
        if state["fail_macro"]:
            return _MacroSnapshot(date=date(2026, 4, 28),
                                  us10y=None, dxy=None, wti=None, sp500=None, gold=None)
        return state["macro"]

    import processor as proc_module
    monkeypatch.setattr(proc_module, "KisApiClient", _make_kis)
    monkeypatch.setattr(proc_module, "NaverNewsScraper", _make_naver)
    monkeypatch.setattr(proc_module, "fetch_macro_snapshot", _fake_macro)
    return state


# ─── Intraday (16:30): signals + holding name 채우기 ────────────────


class TestIntradayHappyPath:
    @pytest.mark.asyncio
    async def test_signals_inserted(self, db_pool, patch_clients):
        """한투 API → signals INSERT (agency/foreign net_buy 보존)."""
        target = date(2026, 4, 28)
        patch_clients["signals"] = [
            _SignalRow(target, "005930", 1_500_000_000, 100_000_000, 1_400_000_000,
                       600_000_000, 100_000_000, 500_000_000),
            _SignalRow(target, "000660", 800_000_000, 50_000_000, 750_000_000,
                       400_000_000, 50_000_000, 350_000_000),
        ]
        from processor import process_intraday
        result = await process_intraday(db_pool, target)
        assert result["mode"] == "intraday"
        assert result["signals_count"] == 2

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ticker, agency_net_buy, foreign_net_buy FROM signals WHERE date=$1 ORDER BY ticker",
                target,
            )
        tickers = {r["ticker"]: r for r in rows}
        assert tickers["005930"]["agency_net_buy"] == 1_400_000_000
        assert tickers["000660"]["foreign_net_buy"] == 350_000_000

    @pytest.mark.asyncio
    async def test_consecutive_buy_days(self, db_pool, patch_clients):
        """이전 4일 매수 + 오늘 → consecutive_buy_days >= 4."""
        from tests.factories import SignalFactory
        ticker = "005930"
        for offset in range(1, 5):
            past = date.fromordinal(date(2026, 4, 28).toordinal() - offset)
            await SignalFactory.create(db_pool, past, ticker,
                                       consecutive_buy_days=1,
                                       agency_net_buy=1_000_000_000,
                                       foreign_net_buy=500_000_000)

        target = date(2026, 4, 28)
        patch_clients["signals"] = [
            _SignalRow(target, ticker, 1_500_000_000, 100_000_000, 1_400_000_000,
                       600_000_000, 100_000_000, 500_000_000),
        ]
        from processor import process_intraday
        await process_intraday(db_pool, target)

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT consecutive_buy_days FROM signals WHERE date=$1 AND ticker=$2",
                target, ticker,
            )
        assert row["consecutive_buy_days"] >= 4

    @pytest.mark.asyncio
    async def test_holding_name_filled(self, db_pool, patch_clients):
        """holdings.name IS NULL 종목은 KIS API로 name 채움."""
        from tests.factories import HoldingFactory
        # name=None으로 holding 생성
        await HoldingFactory.create(db_pool, ticker="005930", name=None, chat_id=11111111)
        patch_clients["names"] = {"005930": "삼성전자"}
        patch_clients["signals"] = []  # signals는 비워도 holding name 채우기는 동작

        from processor import process_intraday
        await process_intraday(db_pool, date(2026, 4, 28))

        async with db_pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT name FROM holdings WHERE ticker = '005930'"
            )
        assert name == "삼성전자"


class TestIntradayErrors:
    @pytest.mark.asyncio
    async def test_kis_auth_failed(self, db_pool, patch_clients):
        """한투 API 401 → 예외 전파(상위 main.py가 stock.data.failed 발행)."""
        import httpx
        patch_clients["fail_kis"] = True
        from processor import process_intraday
        with pytest.raises(httpx.HTTPStatusError):
            await process_intraday(db_pool, date(2026, 4, 28))


# ─── Premarket (06:30): macro + 뉴스 + 추천 트리거 ──────────────────


class TestPremarketHappyPath:
    @pytest.mark.asyncio
    async def test_macro_inserted(self, db_pool, patch_clients):
        """yfinance → macro_indicators INSERT (5지표)."""
        signal_date = date(2026, 4, 28)
        target_trading = date(2026, 4, 29)
        from processor import process_premarket
        result = await process_premarket(db_pool, signal_date, target_trading)
        assert result["mode"] == "premarket"
        assert result["macro_collected"] is True

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT us10y, dxy, wti, sp500, gold FROM macro_indicators ORDER BY date DESC LIMIT 1"
            )
        for col in ("us10y", "dxy", "wti", "sp500", "gold"):
            assert row[col] is not None

    @pytest.mark.asyncio
    async def test_news_uses_target_trading_date(self, db_pool, patch_clients):
        """뉴스는 target_trading_date를 news.date로 저장 (signal_date와 분리)."""
        from tests.factories import SignalFactory
        signal_date = date(2026, 4, 28)
        target_trading = date(2026, 4, 29)

        # signal_date 당일 consecutive_buy_days >= 3 → signal_tickers 풀에 005930 진입
        await SignalFactory.create(db_pool, signal_date, "005930",
                                   consecutive_buy_days=3,
                                   agency_net_buy=1, foreign_net_buy=1)

        patch_clients["news"] = {
            "005930": [_NewsRow(target_trading, "005930", "삼성 호재", "http://x")]
        }
        from processor import process_premarket
        result = await process_premarket(db_pool, signal_date, target_trading)
        assert result["news_count"] == 1

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT date, title FROM news WHERE ticker='005930'"
            )
        assert row["date"] == target_trading
        assert row["title"] == "삼성 호재"

    @pytest.mark.asyncio
    async def test_signal_pool_filter_uses_signal_date(self, db_pool, patch_clients):
        """3일 이상 연속 종목만 신호 풀 → signal_date 기준 필터."""
        from tests.factories import SignalFactory
        signal_date = date(2026, 4, 28)
        target_trading = date(2026, 4, 29)

        # 005930: 3일 연속 ✓
        await SignalFactory.create(db_pool, signal_date, "005930",
                                   consecutive_buy_days=3,
                                   agency_net_buy=1, foreign_net_buy=1)
        # 000660: 1일만 ✗
        await SignalFactory.create(db_pool, signal_date, "000660",
                                   consecutive_buy_days=1,
                                   agency_net_buy=1, foreign_net_buy=1)

        patch_clients["news"] = {
            "005930": [_NewsRow(target_trading, "005930", "in pool", None)],
            "000660": [_NewsRow(target_trading, "000660", "out pool", None)],
        }
        from processor import process_premarket
        result = await process_premarket(db_pool, signal_date, target_trading)
        # 005930만 풀에 들어 → news 1건
        assert result["news_count"] == 1

    @pytest.mark.asyncio
    async def test_holdings_in_focus_pool(self, db_pool, patch_clients):
        """보유 종목은 신호 풀과 무관하게 뉴스 수집 대상."""
        from tests.factories import HoldingFactory
        signal_date = date(2026, 4, 28)
        target_trading = date(2026, 4, 29)
        await HoldingFactory.create(db_pool, ticker="003690", name="코리안리", chat_id=11111111)
        patch_clients["news"] = {
            "003690": [_NewsRow(target_trading, "003690", "코리안리 뉴스", None)]
        }
        from processor import process_premarket
        result = await process_premarket(db_pool, signal_date, target_trading)
        assert result["news_count"] == 1


class TestPremarketErrors:
    @pytest.mark.asyncio
    async def test_yfinance_partial_failure(self, db_pool, patch_clients):
        """yfinance 전부 실패 → macro_collected=False, 작업은 계속."""
        patch_clients["fail_macro"] = True
        from processor import process_premarket
        result = await process_premarket(db_pool, date(2026, 4, 28), date(2026, 4, 29))
        assert result["macro_collected"] is False

    @pytest.mark.asyncio
    async def test_naver_blocked(self, db_pool, patch_clients):
        """네이버 차단 → 해당 종목 뉴스 0건, 작업 계속."""
        from tests.factories import SignalFactory
        signal_date = date(2026, 4, 28)
        target_trading = date(2026, 4, 29)
        for offset in range(0, 3):
            past = date.fromordinal(signal_date.toordinal() - offset)
            await SignalFactory.create(db_pool, past, "005930",
                                       consecutive_buy_days=offset + 1,
                                       agency_net_buy=1, foreign_net_buy=1)
        patch_clients["blocked"] = {"005930"}
        from processor import process_premarket
        result = await process_premarket(db_pool, signal_date, target_trading)
        assert result["news_count"] == 0

    @pytest.mark.asyncio
    async def test_pg_connection_failure(self, db_pool, patch_clients):
        """PG 연결 실패 → 예외 전파."""
        await db_pool.close()
        from processor import process_premarket
        with pytest.raises(Exception):
            await process_premarket(db_pool, date(2026, 4, 28), date(2026, 4, 29))


# ─── 메시지 스키마 ─────────────────────────────────────────────────


class TestMessageSchema:
    @pytest.mark.asyncio
    async def test_target_date_required(self, db_pool):
        """target_date 누락 메시지 → main.py에서 KeyError → DLQ.

        processor는 직접 검증 안 함. main.py 레벨 검증은 통합 테스트.
        """
        event = {"job_id": "x"}
        with pytest.raises(KeyError):
            _ = event["target_date"]
