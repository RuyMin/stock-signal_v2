"""data-collector 처리 로직 (2-cron 분리 버전).

mode='intraday' (KST 16:30):
1. 한투 API → signals (수급 시계열)
2. consecutive_buy_days 계산
3. holdings.name 미설정인 종목은 KIS API로 name 채움

mode='premarket' (KST 06:30, D+1):
1. yfinance → macro_indicators (전일 미국 종가)
2. signals 풀(직전 거래일 기준 3일+ 연속 순매수) + holdings 합집합
3. 네이버 뉴스 스크래핑 (news.date = target_trading_date)
4. main.py가 stock.data.completed 발행 → CrewAI 추천 트리거

부분 실패는 warning 로그 + 카운트 0으로 표기.
"""
import os
from datetime import date, timedelta
from typing import Optional

import asyncpg
import structlog

from clients.kis_api import KisApiClient, SignalRow
from clients.naver_scraper import NaverNewsScraper
from clients.yfinance_client import MacroSnapshot, fetch_macro_snapshot

logger = structlog.get_logger()


def _make_kis() -> KisApiClient:
    return KisApiClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        base_url=os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
    )


async def process_intraday(
    pool: asyncpg.Pool, target_date: date
) -> dict[str, int | bool | str]:
    """16:30 트리거. signals 수집 + holding name 채우기."""
    kis = _make_kis()
    try:
        logger.info("step_kis_api_start")
        signals = await kis.fetch_signals(target_date)
        logger.info("step_kis_api_complete", count=len(signals))
        signals_count = await _save_signals(pool, signals, target_date)
        await _fill_holding_names(pool, kis)
        return {
            "mode": "intraday",
            "signals_count": signals_count,
            "target_date": target_date.isoformat(),
        }
    finally:
        await kis.aclose()


async def process_premarket(
    pool: asyncpg.Pool, signal_date: date, target_trading_date: date
) -> dict[str, int | bool | str]:
    """06:30 트리거. macro + 뉴스 수집 후 추천 트리거 페이로드 반환.

    signal_date: signals 조회 기준일 (직전 한국 거래일).
    target_trading_date: 추천 대상일 (오늘 KST = 장 시작 예정일).
    """
    kis = _make_kis()
    naver = NaverNewsScraper()
    try:
        logger.info("step_yfinance_start")
        macro = await fetch_macro_snapshot()
        macro_collected = await _save_macro(pool, macro)
        logger.info("step_yfinance_complete", collected=macro_collected)

        signal_tickers = await _signal_tickers(pool, signal_date)
        holdings_tickers = await _holdings_tickers(pool)
        focus_tickers = sorted(set(signal_tickers) | set(holdings_tickers))
        logger.info(
            "step_naver_scrape_start",
            signal_count=len(signal_tickers),
            holdings_count=len(holdings_tickers),
            focus_count=len(focus_tickers),
            news_date=target_trading_date.isoformat(),
        )
        news_count = await _scrape_news(pool, naver, focus_tickers, target_trading_date)
        logger.info("step_naver_scrape_complete", count=news_count)

        # holdings에 신규 추가됐을 수 있으니 한 번 더 채움(저비용 — name IS NULL만 조회)
        await _fill_holding_names(pool, kis)

        return {
            "mode": "premarket",
            "news_count": news_count,
            "macro_collected": macro_collected,
            "signal_date": signal_date.isoformat(),
            "target_trading_date": target_trading_date.isoformat(),
        }
    finally:
        await kis.aclose()
        await naver.aclose()


async def _save_signals(
    pool: asyncpg.Pool, signals: list[SignalRow], target_date: date
) -> int:
    if not signals:
        return 0
    async with pool.acquire() as conn, conn.transaction():
        for s in signals:
            consec = await _calc_consecutive_buy_days(conn, s.ticker, target_date)
            await conn.execute(
                """
                INSERT INTO signals (
                    date, ticker, agency_buy, agency_sell, agency_net_buy,
                    foreign_buy, foreign_sell, foreign_net_buy, consecutive_buy_days
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (date, ticker) DO UPDATE SET
                    agency_buy = EXCLUDED.agency_buy,
                    agency_sell = EXCLUDED.agency_sell,
                    agency_net_buy = EXCLUDED.agency_net_buy,
                    foreign_buy = EXCLUDED.foreign_buy,
                    foreign_sell = EXCLUDED.foreign_sell,
                    foreign_net_buy = EXCLUDED.foreign_net_buy,
                    consecutive_buy_days = EXCLUDED.consecutive_buy_days
                """,
                s.date, s.ticker,
                s.agency_buy, s.agency_sell, s.agency_net_buy,
                s.foreign_buy, s.foreign_sell, s.foreign_net_buy,
                consec,
            )
    return len(signals)


async def _calc_consecutive_buy_days(
    conn: asyncpg.Connection, ticker: str, target_date: date
) -> int:
    """오늘 기준 연속 순매수 일수.
    기관 OR 외국인이 순매수면 그날을 '매수일'로 카운트.
    """
    rows = await conn.fetch(
        """
        SELECT date, agency_net_buy, foreign_net_buy
        FROM signals
        WHERE ticker = $1 AND date < $2
        ORDER BY date DESC
        LIMIT 30
        """,
        ticker, target_date,
    )
    consec = 1  # 오늘 신호 종목으로 들어왔다는 가정 → 최소 1
    for r in rows:
        if (r["agency_net_buy"] or 0) > 0 or (r["foreign_net_buy"] or 0) > 0:
            consec += 1
        else:
            break
    return consec


async def _save_macro(pool: asyncpg.Pool, macro: MacroSnapshot) -> bool:
    if all(getattr(macro, f) is None for f in ["us10y", "dxy", "wti", "sp500", "gold"]):
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO macro_indicators (date, us10y, dxy, wti, sp500, gold)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (date) DO UPDATE SET
                us10y = EXCLUDED.us10y,
                dxy = EXCLUDED.dxy,
                wti = EXCLUDED.wti,
                sp500 = EXCLUDED.sp500,
                gold = EXCLUDED.gold
            """,
            macro.date, macro.us10y, macro.dxy, macro.wti, macro.sp500, macro.gold,
        )
    return True


async def _signal_tickers(pool: asyncpg.Pool, signal_date: date) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker FROM signals
            WHERE date = $1 AND consecutive_buy_days >= 3
            """,
            signal_date,
        )
    return [r["ticker"] for r in rows]


async def _holdings_tickers(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ticker FROM holdings")
    return [r["ticker"] for r in rows]


async def _scrape_news(
    pool: asyncpg.Pool,
    naver: NaverNewsScraper,
    tickers: list[str],
    news_date: date,
) -> int:
    inserted = 0
    async with pool.acquire() as conn:
        for ticker in tickers:
            news_items = await naver.fetch_for_ticker(ticker, news_date)
            for item in news_items:
                await conn.execute(
                    """
                    INSERT INTO news (date, ticker, title, url, source)
                    VALUES ($1, $2, $3, $4, 'naver')
                    """,
                    item.date, item.ticker, item.title, item.url,
                )
                inserted += 1
    return inserted


async def _fill_holding_names(pool: asyncpg.Pool, kis: KisApiClient) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ticker FROM holdings WHERE name IS NULL")
        for r in rows:
            ticker = r["ticker"]
            name = await kis.fetch_ticker_name(ticker)
            if name:
                await conn.execute(
                    "UPDATE holdings SET name = $1 WHERE ticker = $2", name, ticker
                )
                logger.info("holding_name_filled", ticker=ticker, name=name)


def _next_business_day(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n
