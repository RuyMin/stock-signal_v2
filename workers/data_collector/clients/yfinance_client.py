"""yfinance 매크로 5지표 수집.

심볼:
- US10Y → ^TNX
- DXY   → DX-Y.NYB
- WTI   → CL=F
- SP500 → ^GSPC
- Gold  → GC=F

한국 장 마감 시점(15:30 KST = 06:30 UTC)에는 직전 미국 거래일 종가가 가장 신선.
yfinance는 동기 라이브러리이므로 asyncio.to_thread로 감싼다.
"""
import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Optional

import structlog
import yfinance as yf

logger = structlog.get_logger()

SYMBOL_MAP = {
    "us10y": "^TNX",
    "dxy": "DX-Y.NYB",
    "wti": "CL=F",
    "sp500": "^GSPC",
    "gold": "GC=F",
}


@dataclass(slots=True)
class MacroSnapshot:
    date: date
    us10y: Optional[float] = None
    dxy: Optional[float] = None
    wti: Optional[float] = None
    sp500: Optional[float] = None
    gold: Optional[float] = None


@dataclass(slots=True)
class TechnicalIndicators:
    """기술적 지표 스냅샷 — yfinance에서 계산."""

    ticker: str
    date: date

    # 당일 종가 (one_day_net_buy 금액 환산용 + trading_value 계산용)
    close: Optional[float] = None

    # Volume indicators
    volume: Optional[int] = None
    volume_20d_avg: Optional[float] = None
    volume_ratio: Optional[float] = None

    # RSI
    rsi: Optional[float] = None

    # Moving averages
    ma_5d: Optional[float] = None
    ma_20d: Optional[float] = None
    ma_60d: Optional[float] = None
    ma_alignment: Optional[str] = None  # 'bullish' | 'bearish' | 'neutral'

    # Bollinger Bands
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_position: Optional[float] = None

    # Trading value
    trading_value: Optional[int] = None


def _fetch_one_sync(symbol: str) -> tuple[Optional[date], Optional[float]]:
    """단일 심볼 최근 종가."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None, None
        last = hist.tail(1)
        d = last.index[-1].date()
        close = float(last["Close"].iloc[-1])
        return d, close
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance_fetch_failed", symbol=symbol, error=str(exc))
        return None, None


async def fetch_macro_snapshot() -> MacroSnapshot:
    """5지표 모두 조회. 일부 실패해도 계속 진행."""
    results = await asyncio.gather(
        *(asyncio.to_thread(_fetch_one_sync, sym) for sym in SYMBOL_MAP.values())
    )
    keys = list(SYMBOL_MAP.keys())
    values: dict[str, Optional[float]] = {}
    snap_date: Optional[date] = None
    for k, (d, v) in zip(keys, results, strict=True):
        values[k] = v
        if d is not None and (snap_date is None or d > snap_date):
            snap_date = d

    if snap_date is None:
        # 모두 실패 — 호출자가 macro_collected=false로 처리
        from datetime import datetime, timezone
        snap_date = datetime.now(timezone.utc).date()

    logger.info(
        "yfinance_snapshot_collected",
        date=snap_date.isoformat(),
        filled={k: v is not None for k, v in values.items()},
    )
    return MacroSnapshot(date=snap_date, **values)


def _calculate_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Calculate RSI using Wilder's smoothing method.
    
    Args:
        prices: List of closing prices (oldest to newest)
        period: RSI period (default 14)
    
    Returns:
        RSI value (0-100) or None if insufficient data
    """
    if len(prices) < period + 1:
        return None
    
    # Calculate price changes
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    
    # Separate gains and losses
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    # Calculate initial average gain and loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Apply Wilder's smoothing for remaining periods
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    # Calculate RSI
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    # Round to 2 decimal places and validate range
    rsi = round(rsi, 2)
    if rsi < 0 or rsi > 100:
        return None
    
    return rsi


def _calculate_ma_alignment(ma_5d: Optional[float], ma_20d: Optional[float], 
                            ma_60d: Optional[float]) -> Optional[str]:
    """Determine moving average alignment.
    
    Args:
        ma_5d: 5-day moving average
        ma_20d: 20-day moving average
        ma_60d: 60-day moving average
    
    Returns:
        'bullish', 'bearish', 'neutral', or None if any MA is missing
    """
    if ma_5d is None or ma_20d is None or ma_60d is None:
        return None
    
    if ma_5d > ma_20d > ma_60d:
        return "bullish"
    elif ma_5d < ma_20d < ma_60d:
        return "bearish"
    else:
        return "neutral"


def _yf_symbol_candidates(ticker: str) -> list[str]:
    """yfinance 심볼 후보 — 6자리 숫자 ticker는 .KS → .KQ 순으로 fallback."""
    if "." in ticker:
        return [ticker]
    if ticker.isdigit() and len(ticker) == 6:
        return [f"{ticker}.KS", f"{ticker}.KQ"]
    return [ticker]


def _fetch_technical_indicators_sync(ticker: str, target_date: date) -> TechnicalIndicators:
    """Fetch technical indicators for a single ticker (synchronous).

    Fetches 60 days of historical data and calculates:
    - Volume ratio (current / 20-day average)
    - RSI (14-period)
    - Moving averages (5, 20, 60 day)
    - MA alignment (bullish/bearish/neutral)
    - Bollinger Bands (20-period, 2 std dev)
    - Trading value in KRW

    Args:
        ticker: Stock ticker symbol. 6자리 숫자(예: "005930")면 .KS/.KQ 자동 시도,
                yfinance 형식(예: "005930.KS")이면 그대로 사용.
        target_date: Date to fetch indicators for

    Returns:
        TechnicalIndicators with calculated values or NULL for failures
    """
    result = TechnicalIndicators(ticker=ticker, date=target_date)

    try:
        # Fetch 60 days of historical data — .KS → .KQ fallback
        hist = None
        for symbol in _yf_symbol_candidates(ticker):
            stock = yf.Ticker(symbol)
            attempt = stock.history(period="60d", interval="1d")
            if not attempt.empty:
                hist = attempt
                break

        if hist is None or hist.empty:
            logger.warning("yfinance_no_data", ticker=ticker, target_date=target_date.isoformat())
            return result
        
        # Convert to lists for easier calculation
        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        
        # Get current day values (last in the list)
        current_close = closes[-1]
        current_volume = int(volumes[-1])
        result.close = round(float(current_close), 2)
        result.volume = current_volume
        
        if len(closes) < 20:
            logger.warning("yfinance_insufficient_data", ticker=ticker, 
                          days=len(closes), required=20)
            # Still return result with volume set, but other indicators will be None
            return result
        
        # Calculate volume ratio
        if len(volumes) >= 20:
            volume_20d_avg = sum(volumes[-20:]) / 20
            result.volume_20d_avg = volume_20d_avg
            if volume_20d_avg > 0:
                result.volume_ratio = round(current_volume / volume_20d_avg, 2)
        
        # Calculate RSI
        if len(closes) >= 15:  # Need at least 15 days for 14-period RSI
            result.rsi = _calculate_rsi(closes)
        
        # Calculate moving averages
        if len(closes) >= 5:
            result.ma_5d = round(sum(closes[-5:]) / 5, 2)
        
        if len(closes) >= 20:
            result.ma_20d = round(sum(closes[-20:]) / 20, 2)
        
        if len(closes) >= 60:
            result.ma_60d = round(sum(closes[-60:]) / 60, 2)
        
        # Determine MA alignment
        result.ma_alignment = _calculate_ma_alignment(result.ma_5d, result.ma_20d, result.ma_60d)
        
        # Calculate Bollinger Bands (20-period, 2 std dev)
        if len(closes) >= 20:
            bb_period = closes[-20:]
            bb_mean = sum(bb_period) / 20
            bb_variance = sum((x - bb_mean) ** 2 for x in bb_period) / 20
            bb_std = bb_variance ** 0.5
            
            result.bb_upper = round(bb_mean + 2 * bb_std, 2)
            result.bb_lower = round(bb_mean - 2 * bb_std, 2)
            
            # Calculate position within bands
            if result.bb_upper != result.bb_lower:
                bb_position = (current_close - result.bb_lower) / (result.bb_upper - result.bb_lower)
                # Validate range and round to 3 decimal places
                if 0 <= bb_position <= 1:
                    result.bb_position = round(bb_position, 3)
        
        # Calculate trading value (volume * close price)
        # Note: yfinance returns volume in shares, need to multiply by price
        result.trading_value = int(current_volume * current_close)
        
        logger.info(
            "technical_indicators_fetched",
            ticker=ticker,
            rsi=result.rsi,
            ma_alignment=result.ma_alignment,
            volume_ratio=result.volume_ratio,
            bb_position=result.bb_position
        )
        
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "technical_indicator_fetch_failed",
            ticker=ticker,
            error=str(exc),
            error_type=type(exc).__name__
        )
    
    return result


async def fetch_technical_indicators(ticker: str, target_date: date) -> TechnicalIndicators:
    """Fetch technical indicators for a single ticker (async wrapper).
    
    Uses asyncio.to_thread to wrap the synchronous yfinance calls.
    
    Args:
        ticker: Stock ticker symbol (e.g., "005930.KS" for Samsung)
        target_date: Date to fetch indicators for
    
    Returns:
        TechnicalIndicators with calculated values or NULL for failures
    """
    return await asyncio.to_thread(_fetch_technical_indicators_sync, ticker, target_date)
