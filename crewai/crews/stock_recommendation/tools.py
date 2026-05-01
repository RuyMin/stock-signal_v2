"""StockRecommendationCrew 전용 Tool 모음.

모두 READ-only. 결과 INSERT는 BaseCrew.on_complete()에서 처리.
모든 Tool은 동기 — psycopg3 sync pool 사용.
출력은 JSON 문자열 (CrewAI Agent가 LLM context로 받음).
"""
import json
from datetime import date as date_type

import structlog
from pydantic import BaseModel, Field

from core.base_tool import VibeBaseTool
from core.db import get_pool

logger = structlog.get_logger()


# ─── SignalQueryTool ─────────────────────────────────


class SignalQueryInput(BaseModel):
    target_date: str = Field(description="조회 기준일 (YYYY-MM-DD)")
    min_consecutive: int = Field(
        default=3,
        description="최소 연속 매수일 (기본 3, PRD 비즈니스 규칙)",
    )


class SignalQueryTool(VibeBaseTool):
    name: str = "signal_query"
    description: str = (
        "특정 거래일에 기관/외국인이 N일 이상 연속 순매수한 종목과 수급 데이터를 조회한다. "
        "추천 후보 풀을 결정할 때 사용."
    )
    args_schema: type[BaseModel] = SignalQueryInput

    def _run(self, target_date: str, min_consecutive: int = 3) -> str:
        try:
            d = date_type.fromisoformat(target_date)
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker, agency_net_buy, foreign_net_buy,
                           agency_buy, agency_sell, foreign_buy, foreign_sell,
                           consecutive_buy_days
                    FROM signals
                    WHERE date = %s AND consecutive_buy_days >= %s
                    ORDER BY consecutive_buy_days DESC, (COALESCE(agency_net_buy,0)
                            + COALESCE(foreign_net_buy,0)) DESC
                    """,
                    (d, min_consecutive),
                )
                rows = [
                    {
                        "ticker": r[0],
                        "agency_net_buy": r[1],
                        "foreign_net_buy": r[2],
                        "agency_buy": r[3],
                        "agency_sell": r[4],
                        "foreign_buy": r[5],
                        "foreign_sell": r[6],
                        "consecutive_buy_days": r[7],
                    }
                    for r in cur.fetchall()
                ]
            return self.ok(json.dumps({"count": len(rows), "items": rows}))
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))


# ─── NewsQueryTool ────────────────────────────────────


class NewsQueryInput(BaseModel):
    target_date: str = Field(description="조회 기준일 (YYYY-MM-DD)")
    tickers: list[str] = Field(description="조회 대상 종목코드 목록")


class NewsQueryTool(VibeBaseTool):
    name: str = "news_query"
    description: str = (
        "지정 종목 목록의 당일 뉴스 헤드라인을 조회한다. "
        "감성 분석을 위한 입력으로 사용."
    )
    args_schema: type[BaseModel] = NewsQueryInput

    def _run(self, target_date: str, tickers: list[str]) -> str:
        try:
            d = date_type.fromisoformat(target_date)
            if not tickers:
                return self.ok(json.dumps({"count": 0, "items": []}))
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker, title, url
                    FROM news
                    WHERE date = %s AND ticker = ANY(%s)
                    ORDER BY ticker, collected_at DESC
                    """,
                    (d, tickers),
                )
                rows = [{"ticker": r[0], "title": r[1], "url": r[2]} for r in cur.fetchall()]
            return self.ok(json.dumps({"count": len(rows), "items": rows}))
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))


# ─── MacroQueryTool ──────────────────────────────────


class MacroQueryInput(BaseModel):
    near_date: str = Field(
        description="기준일 — 이 날짜 이전의 가장 최근 매크로 지표를 가져옴 (YYYY-MM-DD)",
    )


class MacroQueryTool(VibeBaseTool):
    name: str = "macro_query"
    description: str = (
        "가장 최근 미국 장 종가 기준 매크로 5지표(US10Y / DXY / WTI / S&P500 / Gold)를 조회. "
        "추천 발행 시점에는 보통 전일 미국 종가가 가장 신선한 데이터다."
    )
    args_schema: type[BaseModel] = MacroQueryInput

    def _run(self, near_date: str) -> str:
        try:
            d = date_type.fromisoformat(near_date)
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date, us10y, dxy, wti, sp500, gold
                    FROM macro_indicators
                    WHERE date <= %s
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (d,),
                )
                row = cur.fetchone()
            if row is None:
                return self.ok(json.dumps({"available": False}))
            return self.ok(
                json.dumps(
                    {
                        "available": True,
                        "date": row[0].isoformat(),
                        "us10y": float(row[1]) if row[1] is not None else None,
                        "dxy": float(row[2]) if row[2] is not None else None,
                        "wti": float(row[3]) if row[3] is not None else None,
                        "sp500": float(row[4]) if row[4] is not None else None,
                        "gold": float(row[5]) if row[5] is not None else None,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))


# ─── HoldingsQueryTool (공통 — Synthesizer가 탈출 경보 분류 시 사용) ─


class HoldingsQueryInput(BaseModel):
    pass


class HoldingsQueryTool(VibeBaseTool):
    name: str = "holdings_query"
    description: str = (
        "전체 사용자(소규모 화이트리스트 multi-user)가 등록한 보유 종목의 합집합을 조회. "
        "탈출 경보(exit_alert) 후보 풀로 사용 — 어느 사용자라도 보유한 종목 중에서만 분류. "
        "사용자별 메시지 분기(\"보유\" 표기 vs 미보유 종목 제외)는 notifier가 후처리한다."
    )
    args_schema: type[BaseModel] = HoldingsQueryInput

    def _run(self) -> str:  # type: ignore[override]
        try:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT ticker, name FROM holdings ORDER BY ticker")
                rows = [{"ticker": r[0], "name": r[1]} for r in cur.fetchall()]
            return self.ok(json.dumps({"count": len(rows), "tickers": [r["ticker"] for r in rows]}))
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))
