"""WeeklyMacroReportCrew 전용 Tool.

- MacroWeeklyQueryTool: 매크로 5지표 주간 시작/종료값 + 변화율
- ETFHoldingsQueryTool: ETF 보유 종목 + 사용자 chat_id + 추종 지수
"""
import json
from datetime import date as date_type
from datetime import timedelta
from typing import Optional

import structlog
from pydantic import BaseModel, Field

from core.base_tool import VibeBaseTool
from core.db import get_pool

from .etf_mapping import tracking_index

logger = structlog.get_logger()


# ─── MacroWeeklyQueryTool ─────────────────────────────


class MacroWeeklyQueryInput(BaseModel):
    week_start: str = Field(description="주 시작일 YYYY-MM-DD (보통 직전 월요일)")
    week_end: str = Field(description="주 종료일 YYYY-MM-DD (보통 오늘 또는 보고일)")


class MacroWeeklyQueryTool(VibeBaseTool):
    name: str = "macro_weekly_query"
    description: str = (
        "주간 매크로 5지표(us10y, dxy, wti, sp500, gold) 시작/종료 값 + 절대/비율 변화 조회. "
        "시작값: week_start 이하 가장 최근 row. 종료값: week_end 이하 가장 최근 row. "
        "데이터 없으면 해당 지표에 null."
    )
    args_schema: type[BaseModel] = MacroWeeklyQueryInput

    _INDICATORS = ("us10y", "dxy", "wti", "sp500", "gold")

    def _run(self, week_start: str, week_end: str) -> str:
        try:
            d_start = date_type.fromisoformat(week_start)
            d_end = date_type.fromisoformat(week_end)
            indicators = []
            with get_pool().connection() as conn, conn.cursor() as cur:
                for ind in self._INDICATORS:
                    # 시작값
                    cur.execute(
                        f"SELECT {ind} FROM macro_indicators "
                        f"WHERE date <= %s AND {ind} IS NOT NULL "
                        "ORDER BY date DESC LIMIT 1",
                        (d_start,),
                    )
                    row = cur.fetchone()
                    start_val = float(row[0]) if row and row[0] is not None else None

                    # 종료값
                    cur.execute(
                        f"SELECT {ind} FROM macro_indicators "
                        f"WHERE date <= %s AND {ind} IS NOT NULL "
                        "ORDER BY date DESC LIMIT 1",
                        (d_end,),
                    )
                    row = cur.fetchone()
                    end_val = float(row[0]) if row and row[0] is not None else None

                    delta_abs = None
                    delta_pct = None
                    if start_val is not None and end_val is not None and start_val != 0:
                        delta_abs = round(end_val - start_val, 4)
                        delta_pct = round((end_val - start_val) / abs(start_val) * 100, 2)

                    indicators.append({
                        "name": ind,
                        "start": start_val,
                        "end": end_val,
                        "delta_abs": delta_abs,
                        "delta_pct": delta_pct,
                    })

            return self.ok(json.dumps({
                "week_start": week_start,
                "week_end": week_end,
                "indicators": indicators,
            }))
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))


# ─── ETFHoldingsQueryTool ─────────────────────────────


class ETFHoldingsQueryInput(BaseModel):
    pass


class ETFHoldingsQueryTool(VibeBaseTool):
    name: str = "etf_holdings_query"
    description: str = (
        "ETF 보유 종목(instrument_type IN 'index_etf'/'sector_etf') + 각 종목의 "
        "추종 지수(tracking_index) + 보유한 active 사용자 chat_id 목록 조회. "
        "tracking_index가 null이면 미매핑 — 일반 매크로 톤 적용."
    )
    args_schema: type[BaseModel] = ETFHoldingsQueryInput

    def _run(self) -> str:  # type: ignore[override]
        try:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT h.ticker, h.name, h.instrument_type, u.chat_id
                    FROM holdings h
                    JOIN users u ON u.id = h.user_id
                    WHERE h.instrument_type IN ('index_etf', 'sector_etf')
                      AND u.status = 'active'
                    ORDER BY h.ticker, u.chat_id
                    """
                )
                rows = cur.fetchall()

            # ticker별로 그룹화 + tracking_index 부여
            by_ticker: dict[str, dict] = {}
            for ticker, name, inst_type, chat_id in rows:
                entry = by_ticker.setdefault(ticker, {
                    "ticker": ticker,
                    "name": name,
                    "instrument_type": inst_type,
                    "tracking_index": tracking_index(ticker),
                    "holder_chat_ids": [],
                })
                entry["holder_chat_ids"].append(int(chat_id))

            items = list(by_ticker.values())
            return self.ok(json.dumps({"count": len(items), "items": items}))
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))
