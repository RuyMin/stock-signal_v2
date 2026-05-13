"""StockRecommendationCrew 전용 Tool 모음.

모두 READ-only. 결과 INSERT는 BaseCrew.on_complete()에서 처리.
모든 Tool은 동기 — psycopg3 sync pool 사용.
출력은 JSON 문자열 (CrewAI Agent가 LLM context로 받음).
"""
import json
from datetime import date as date_type
from typing import Optional

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
        description="최소 연속 매수일 (기본 3, PRD 비즈니스 규칙). 보유 종목 강도 평가 시 0 사용.",
    )
    tickers: Optional[list[str]] = Field(
        default=None,
        description="지정 시 해당 ticker만 반환 (보유 종목 강도 평가용). 미지정 시 모든 ticker.",
    )


class SignalQueryTool(VibeBaseTool):
    name: str = "signal_query"
    description: str = (
        "특정 거래일의 수급 데이터를 조회한다. 두 가지 사용 패턴:\n"
        "1. 신규 매수 후보 도출: min_consecutive=3 (또는 PRD 기준값), tickers=None — 강한 시그널만.\n"
        "2. 보유 종목 강도 평가: min_consecutive=0, tickers=[보유 종목 목록] — "
        "consecutive 무관하게 실제 net_buy 등 데이터 반환.\n"
        "결과 row가 없으면 그 ticker는 해당 거래일에 데이터 자체가 없음(휴장 또는 미수집)."
    )
    args_schema: type[BaseModel] = SignalQueryInput

    def _run(
        self,
        target_date: str,
        min_consecutive: int = 3,
        tickers: Optional[list[str]] = None,
    ) -> str:
        try:
            d = date_type.fromisoformat(target_date)
            sql = (
                "SELECT ticker, agency_net_buy, foreign_net_buy, "
                "agency_buy, agency_sell, foreign_buy, foreign_sell, "
                "consecutive_buy_days FROM signals "
                "WHERE date = %s AND consecutive_buy_days >= %s"
            )
            params: list = [d, min_consecutive]
            if tickers:
                sql += " AND ticker = ANY(%s)"
                params.append(tickers)
            sql += (
                " ORDER BY consecutive_buy_days DESC, "
                "(COALESCE(agency_net_buy,0) + COALESCE(foreign_net_buy,0)) DESC"
            )
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
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
    date_from: str = Field(description="조회 시작일 (YYYY-MM-DD, 포함)")
    date_to: str = Field(description="조회 종료일 (YYYY-MM-DD, 포함)")
    tickers: list[str] = Field(description="조회 대상 종목코드 목록")


class NewsQueryTool(VibeBaseTool):
    name: str = "news_query"
    description: str = (
        "지정 종목 목록의 [date_from, date_to] 범위 뉴스 헤드라인을 조회한다. "
        "휴장일이 끼어 있어도 직전 거래일~다음 거래일 사이 뉴스를 모두 확인할 수 있도록 범위 쿼리. "
        "각 항목에 date 필드가 포함되어 시점 구분이 가능하다."
    )
    args_schema: type[BaseModel] = NewsQueryInput

    def _run(self, date_from: str, date_to: str, tickers: list[str]) -> str:
        try:
            d_from = date_type.fromisoformat(date_from)
            d_to = date_type.fromisoformat(date_to)
            if d_to < d_from:
                d_from, d_to = d_to, d_from
            if not tickers:
                return self.ok(json.dumps({"count": 0, "items": []}))
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker, date, title, url
                    FROM news
                    WHERE date BETWEEN %s AND %s AND ticker = ANY(%s)
                    ORDER BY ticker, date DESC, collected_at DESC
                    """,
                    (d_from, d_to, tickers),
                )
                rows = [
                    {
                        "ticker": r[0],
                        "date": r[1].isoformat(),
                        "title": r[2],
                        "url": r[3],
                    }
                    for r in cur.fetchall()
                ]
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


# ─── MomentumQueryTool ───────────────────────────────


class MomentumQueryInput(BaseModel):
    target_date: str = Field(description="조회 기준일 (YYYY-MM-DD)")
    tickers: Optional[list[str]] = Field(
        default=None,
        description="지정 시 해당 ticker만 반환 (후보 평가용). 미지정 시 그 날짜의 모든 ticker.",
    )


class MomentumQueryTool(VibeBaseTool):
    name: str = "momentum_query"
    description: str = (
        "특정 거래일의 모멘텀/기술적 지표를 조회한다. 반환 필드:\n"
        "- one_day_net_buy: 1일 순매수 금액 (원/KRW, 기관+외국인 합계). surge momentum 임계 100억(10000000000).\n"
        "- three_day_avg_net_buy: 직전 3거래일 평균 순매수 금액 (원). acceleration: one_day >= 2*avg AND avg>0.\n"
        "- volume_ratio: 당일 거래량/20일 평균 거래량. volume surge 임계 3.0.\n"
        "- rsi: 14기간 RSI (0~100). <30 과매도, >70 과매수.\n"
        "- ma_alignment: 'bullish' | 'bearish' | 'neutral' (5/20/60일선 배열).\n"
        "- bollinger_position: 볼린저밴드 내 위치 (0~1).\n"
        "- trading_value: 거래대금 (원/KRW). 유동성 페널티 임계 50억(5000000000).\n"
        "NULL은 데이터 부족 또는 yfinance 실패를 의미 — 점수 계산 시 0점 처리(spec 12.3)."
    )
    args_schema: type[BaseModel] = MomentumQueryInput

    def _run(
        self,
        target_date: str,
        tickers: Optional[list[str]] = None,
    ) -> str:
        try:
            d = date_type.fromisoformat(target_date)
            sql = (
                "SELECT ticker, one_day_net_buy, three_day_avg_net_buy, "
                "volume_ratio, rsi, ma_alignment, bollinger_position, trading_value "
                "FROM signals WHERE date = %s"
            )
            params: list = [d]
            if tickers:
                sql += " AND ticker = ANY(%s)"
                params.append(tickers)
            sql += " ORDER BY COALESCE(one_day_net_buy, 0) DESC"
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                rows = [
                    {
                        "ticker": r[0],
                        "one_day_net_buy": r[1],
                        "three_day_avg_net_buy": r[2],
                        "volume_ratio": float(r[3]) if r[3] is not None else None,
                        "rsi": float(r[4]) if r[4] is not None else None,
                        "ma_alignment": r[5],
                        "bollinger_position": float(r[6]) if r[6] is not None else None,
                        "trading_value": r[7],
                    }
                    for r in cur.fetchall()
                ]
            return self.ok(json.dumps({"count": len(rows), "items": rows}))
        except Exception as exc:  # noqa: BLE001
            return self.err_unknown(str(exc))


# ─── HoldingsQueryTool ────────────────────────────────


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
