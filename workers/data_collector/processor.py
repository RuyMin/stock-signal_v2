"""data-collector 처리 로직 (2-cron 분리 버전).

mode='intraday' (KST 16:30):
1. 한투 API → signals (수급 시계열)
2. consecutive_buy_days + momentum(one_day/three_day_avg_net_buy) 계산
3. yfinance에서 기술적 지표 fetch → signals UPDATE (volume_ratio/rsi/ma/bb/trading_value)
4. holdings.name 미설정인 종목은 KIS API로 name 채움

mode='premarket' (KST 06:30, D+1):
1. yfinance → macro_indicators (전일 미국 종가)
2. signals 풀(직전 거래일 기준 3일+ 연속 순매수) + holdings 합집합
3. 네이버 뉴스 스크래핑 (news.date = target_trading_date)
4. main.py가 stock.data.completed 발행 → CrewAI 추천 트리거

부분 실패는 warning 로그 + 카운트 0으로 표기.
"""
import asyncio
import os
from datetime import date, timedelta
from typing import Optional

import asyncpg
import structlog

from clients.kis_api import KisApiClient, SignalRow
from clients.naver_scraper import NaverNewsScraper
from clients.yfinance_client import (
    MacroSnapshot,
    TechnicalIndicators,
    fetch_macro_snapshot,
    fetch_technical_indicators,
)

# 기술적 지표 병렬 fetch 동시성 제한 (yfinance rate-limit 대응)
_TECH_INDICATOR_CONCURRENCY = 10

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
    """16:30 트리거. signals + 기술적 지표 통합 수집.

    흐름:
      1. KIS API → 외인/기관 순매수 수량(주)
      2. yfinance → 종가/RSI/MA/BB/거래대금 등 (병렬)
      3. _save_signals → 양쪽을 합쳐 한 번에 INSERT
         (one_day_net_buy = (agency+foreign) × close → 단위: 원/KRW)
    """
    kis = _make_kis()
    try:
        logger.info("step_kis_api_start")
        signals = await kis.fetch_signals(target_date)
        logger.info("step_kis_api_complete", count=len(signals))

        tickers = [s.ticker for s in signals]
        logger.info("step_technical_indicators_start", count=len(tickers))
        tech_map, tech_success, tech_failure = (
            await _fetch_technical_indicators_for_signals(tickers, target_date)
        )
        logger.info(
            "step_technical_indicators_complete",
            success=tech_success,
            failure=tech_failure,
        )

        signals_count = await _save_signals(pool, signals, target_date, tech_map)

        await _fill_holding_names(pool, kis)
        return {
            "mode": "intraday",
            "signals_count": signals_count,
            "technical_indicators_count": tech_success,
            "technical_indicators_failed": tech_failure,
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
    pool: asyncpg.Pool,
    signals: list[SignalRow],
    target_date: date,
    tech_map: dict[str, TechnicalIndicators],
) -> int:
    if not signals:
        return 0
    async with pool.acquire() as conn, conn.transaction():
        for s in signals:
            consec = await _calc_consecutive_buy_days(conn, s.ticker, target_date)
            ti = tech_map.get(s.ticker)
            close = ti.close if ti else None
            one_day_net_buy, three_day_avg = await _calculate_momentum_indicators(
                conn, s.ticker, target_date,
                s.agency_net_buy, s.foreign_net_buy, close,
            )
            await conn.execute(
                """
                INSERT INTO signals (
                    date, ticker, agency_buy, agency_sell, agency_net_buy,
                    foreign_buy, foreign_sell, foreign_net_buy, consecutive_buy_days,
                    one_day_net_buy, three_day_avg_net_buy,
                    volume_ratio, rsi, ma_alignment, bollinger_position, trading_value
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                ON CONFLICT (date, ticker) DO UPDATE SET
                    agency_buy = EXCLUDED.agency_buy,
                    agency_sell = EXCLUDED.agency_sell,
                    agency_net_buy = EXCLUDED.agency_net_buy,
                    foreign_buy = EXCLUDED.foreign_buy,
                    foreign_sell = EXCLUDED.foreign_sell,
                    foreign_net_buy = EXCLUDED.foreign_net_buy,
                    consecutive_buy_days = EXCLUDED.consecutive_buy_days,
                    one_day_net_buy = EXCLUDED.one_day_net_buy,
                    three_day_avg_net_buy = EXCLUDED.three_day_avg_net_buy,
                    volume_ratio = EXCLUDED.volume_ratio,
                    rsi = EXCLUDED.rsi,
                    ma_alignment = EXCLUDED.ma_alignment,
                    bollinger_position = EXCLUDED.bollinger_position,
                    trading_value = EXCLUDED.trading_value
                """,
                s.date, s.ticker,
                s.agency_buy, s.agency_sell, s.agency_net_buy,
                s.foreign_buy, s.foreign_sell, s.foreign_net_buy,
                consec, one_day_net_buy, three_day_avg,
                ti.volume_ratio if ti else None,
                ti.rsi if ti else None,
                ti.ma_alignment if ti else None,
                ti.bb_position if ti else None,
                ti.trading_value if ti else None,
            )
    return len(signals)


async def _calculate_momentum_indicators(
    conn: asyncpg.Connection,
    ticker: str,
    target_date: date,
    agency_net_buy: int,
    foreign_net_buy: int,
    close: Optional[float],
) -> tuple[Optional[int], Optional[int]]:
    """one_day_net_buy = (agency+foreign 수량) × close → 단위: 원(KRW).

    close가 None(yfinance 실패)이면 one_day_net_buy도 None — 단위가 보장 안 되는
    값은 저장하지 않음. spec의 "100억 원" 임계치와 단위를 맞추기 위함.

    three_day_avg는 직전 3거래일치 one_day_net_buy 정확히 3개 있을 때만 평균.
    부족하면 None(NULL) — acceleration 패턴 자동 비활성화.
    """
    if close is None:
        return None, None
    qty = (agency_net_buy or 0) + (foreign_net_buy or 0)
    one_day_net_buy = int(qty * close)

    rows = await conn.fetch(
        """
        SELECT one_day_net_buy
        FROM signals
        WHERE ticker = $1 AND date < $2 AND one_day_net_buy IS NOT NULL
        ORDER BY date DESC
        LIMIT 3
        """,
        ticker, target_date,
    )
    if len(rows) < 3:
        return one_day_net_buy, None
    three_day_avg = sum(r["one_day_net_buy"] for r in rows) // 3
    return one_day_net_buy, three_day_avg


async def _fetch_technical_indicators_for_signals(
    tickers: list[str],
    target_date: date,
) -> tuple[dict[str, TechnicalIndicators], int, int]:
    """yfinance에서 기술적 지표 병렬 fetch. signals INSERT 직전에 호출.

    동시 호출은 semaphore로 제한 (rate-limit 대응). 개별 실패는 격리.
    success 기준: rsi / ma_alignment / bb_position / volume_ratio / trading_value
    중 하나라도 non-NULL.

    Returns: (ticker→TechnicalIndicators map, success, failure)
    """
    if not tickers:
        return {}, 0, 0

    semaphore = asyncio.Semaphore(_TECH_INDICATOR_CONCURRENCY)

    async def _fetch_one(ticker: str) -> Optional[TechnicalIndicators]:
        async with semaphore:
            try:
                return await fetch_technical_indicators(ticker, target_date)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "technical_indicator_fetch_exception",
                    ticker=ticker,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return None

    results = await asyncio.gather(*(_fetch_one(t) for t in tickers))

    tech_map: dict[str, TechnicalIndicators] = {}
    success = 0
    failure = 0
    for ticker, ti in zip(tickers, results):
        if ti is None:
            failure += 1
            continue
        has_any = any(
            v is not None for v in (
                ti.rsi, ti.ma_alignment, ti.bb_position,
                ti.volume_ratio, ti.trading_value,
            )
        )
        if not has_any:
            failure += 1
            logger.warning("technical_indicator_all_null", ticker=ticker)
            continue
        tech_map[ticker] = ti
        success += 1
    return tech_map, success, failure


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
