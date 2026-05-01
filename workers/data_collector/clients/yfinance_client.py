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
