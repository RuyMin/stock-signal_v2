"""StockRecommendationCrew — Sequential 4 Agents + 4 Tasks.

on_complete()에서 LLM이 출력한 JSON을 파싱하여 recommendations INSERT.
실패 시 (파싱 실패 / 빈 결과 등) 명시적 카운트 0으로 발행.
"""
import json
import re
from datetime import date as date_type
from typing import Any

import structlog
from crewai import Process

from clients import kis_api
from core.base_crew import BaseCrew
from core.db import get_pool

from .agents import (
    MacroEnvAgent,
    NewsAnalystAgent,
    SignalAnalyzerAgent,
    SynthesizerAgent,
)
from .scoring import total_score
from .tasks import (
    MacroAnalysisTask,
    NewsAnalysisTask,
    SignalAnalysisTask,
    SynthesisTask,
)

logger = structlog.get_logger()


class StockRecommendationCrew(BaseCrew):
    crew_name = "stock-recommendation"
    version = "1.0.0"
    process = Process.sequential

    def setup_agents(self) -> list:
        return [
            SignalAnalyzerAgent().build(),
            NewsAnalystAgent().build(),
            MacroEnvAgent().build(),
            SynthesizerAgent().build(),
        ]

    def setup_tasks(self, agents: list) -> list:
        signal, news, macro, synth = agents
        signal_task = SignalAnalysisTask().build(agent=signal)
        news_task = NewsAnalysisTask().build(agent=news, context=[signal_task])
        macro_task = MacroAnalysisTask().build(agent=macro)
        synth_task = SynthesisTask().build(
            agent=synth, context=[signal_task, news_task, macro_task]
        )
        return [signal_task, news_task, macro_task, synth_task]

    def on_complete(self, raw_result: Any, inputs: dict) -> dict:
        """Synthesizer 출력(JSON 배열) 파싱 → 코드로 score 산정 → recommendations INSERT.

        점수 산정은 scoring.total_score()가 결정론적으로 계산. LLM은 sentiment +
        macro_verdict + reason만 작성. score는 LLM 출력에서 무시한다 (있어도 사용 안 함).

        분류 룰:
          - score ≥ 70 → buy_hedge
          - 50 ≤ score < 70 → watch
          - score < 50 AND 보유 → exit_alert
          - score < 50 AND 신규 → 제외 (시장 추천 가치 없음)
        """
        target_date = date_type.fromisoformat(inputs["target_date"])
        target_trading_date = date_type.fromisoformat(inputs["target_trading_date"])

        items = _parse_recommendations(raw_result)
        inserted = 0
        has_buy_hedge = False
        has_watch = False
        has_exit_alert = False

        if items:
            with get_pool().connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT ticker FROM holdings")
                    holdings_set = {r[0] for r in cur.fetchall()}

                    # signals row 일괄 fetch (ticker → row dict)
                    tickers = [it["ticker"] for it in items]
                    signal_rows = _fetch_signal_rows(cur, tickers, target_date)

                    # 알려진 이름 캐시: holdings + 최근 recommendations에서 ticker→name
                    # KIS API 5xx 등 실패 시 fallback. NULL row 방지.
                    known_names = _fetch_known_names(cur, tickers)

                    for it in items:
                        ticker = it["ticker"]
                        is_holding = ticker in holdings_set
                        sentiment = it.get("sentiment")
                        macro_verdict = it.get("macro_verdict")

                        # 코드 기반 score 산정 (LLM score는 무시)
                        score, breakdown = total_score(
                            signal_rows.get(ticker), sentiment, macro_verdict
                        )

                        # name 보강: LLM 명시 → KIS API 조회 → 알려진 이름 캐시 → NULL
                        rec_name = (it.get("name") or "").strip() or None
                        if rec_name is None:
                            rec_name = kis_api.fetch_ticker_name(ticker)
                        if rec_name is None:
                            rec_name = known_names.get(ticker)
                            if rec_name:
                                logger.info(
                                    "ticker_name_from_cache",
                                    ticker=ticker, name=rec_name,
                                )

                        # score 기반 분류
                        if score >= 70:
                            rec_type = "buy_hedge"
                        elif score >= 50:
                            rec_type = "watch"
                        elif is_holding:
                            rec_type = "exit_alert"
                        else:
                            logger.info(
                                "skipped_low_score_new_candidate",
                                ticker=ticker, score=score, breakdown=breakdown,
                            )
                            continue

                        cur.execute(
                            """
                            INSERT INTO recommendations (
                                date, target_trading_date, ticker, name,
                                recommendation_type, score,
                                reason_supply, reason_news, reason_macro,
                                estimated_avg_price, job_id
                            ) VALUES (
                                %s, %s, %s, %s,
                                %s, %s,
                                %s, %s, %s,
                                %s, %s
                            )
                            """,
                            (
                                target_date,
                                target_trading_date,
                                ticker,
                                rec_name,
                                rec_type,
                                score,
                                it.get("reason_supply"),
                                it.get("reason_news"),
                                it.get("reason_macro"),
                                it.get("estimated_avg_price") if rec_type == "buy_hedge" else None,
                                self.job_id,
                            ),
                        )
                        inserted += 1
                        has_buy_hedge |= rec_type == "buy_hedge"
                        has_watch |= rec_type == "watch"
                        has_exit_alert |= rec_type == "exit_alert"
                        logger.info(
                            "recommendation_created",
                            ticker=ticker, type=rec_type, score=score,
                            breakdown=breakdown, sentiment=sentiment,
                            macro_verdict=macro_verdict, is_holding=is_holding,
                            has_signal_row=ticker in signal_rows,
                        )
                conn.commit()

        return {
            "job_id": self.job_id,
            "target_trading_date": target_trading_date.isoformat(),
            "recommendation_count": inserted,
            "has_buy_hedge": has_buy_hedge,
            "has_watch": has_watch,
            "has_exit_alert": has_exit_alert,
        }


def _fetch_known_names(cur, tickers: list[str]) -> dict[str, str]:
    """ticker → 알려진 이름. holdings + 최근 recommendations에서 NULL 아닌 이름 수집.
    KIS API 실패 시 fallback. 같은 ticker에 holdings 이름과 recommendations 이름이
    다르면 holdings가 우선 (사용자가 명시한 이름).
    """
    if not tickers:
        return {}
    result: dict[str, str] = {}
    # recommendations 최근 이름 (오래된 것부터 채워 → holdings로 덮어쓰기)
    cur.execute(
        """
        SELECT DISTINCT ON (ticker) ticker, name
        FROM recommendations
        WHERE ticker = ANY(%s) AND name IS NOT NULL
        ORDER BY ticker, created_at DESC
        """,
        (tickers,),
    )
    for r in cur.fetchall():
        result[r[0]] = r[1]
    # holdings 이름 (우선 적용)
    cur.execute(
        "SELECT DISTINCT ON (ticker) ticker, name FROM holdings "
        "WHERE ticker = ANY(%s) AND name IS NOT NULL ORDER BY ticker, added_at DESC",
        (tickers,),
    )
    for r in cur.fetchall():
        result[r[0]] = r[1]
    return result


def _fetch_signal_rows(cur, tickers: list[str], target_date: date_type) -> dict[str, dict]:
    """signals 테이블에서 ticker별 row를 dict 형태로 반환. row 없는 ticker는 키 없음."""
    if not tickers:
        return {}
    cur.execute(
        """
        SELECT ticker, agency_net_buy, foreign_net_buy, consecutive_buy_days,
               one_day_net_buy, three_day_avg_net_buy, volume_ratio,
               rsi, ma_alignment, bollinger_position, trading_value
        FROM signals
        WHERE date = %s AND ticker = ANY(%s)
        """,
        (target_date, tickers),
    )
    rows: dict[str, dict] = {}
    for r in cur.fetchall():
        rows[r[0]] = {
            "agency_net_buy": r[1],
            "foreign_net_buy": r[2],
            "consecutive_buy_days": r[3],
            "one_day_net_buy": r[4],
            "three_day_avg_net_buy": r[5],
            "volume_ratio": float(r[6]) if r[6] is not None else None,
            "rsi": float(r[7]) if r[7] is not None else None,
            "ma_alignment": r[8],
            "bollinger_position": float(r[9]) if r[9] is not None else None,
            "trading_value": r[10],
        }
    return rows


_JSON_BLOCK = re.compile(r"\[.*\]", re.DOTALL)


def _parse_recommendations(raw: Any) -> list[dict]:
    """LLM 출력에서 JSON 배열 추출. 실패 시 빈 리스트.
    (LLM이 마크다운 코드블록 안에 JSON을 감쌀 수 있음 — 정규식으로 추출.)
    """
    text = str(raw).strip()
    if not text or text == "[]":
        return []
    # 1) 통째로 JSON 시도
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [_validate(item) for item in data if _validate(item)]
    except json.JSONDecodeError:
        pass
    # 2) 본문 안에서 [...] 블록 추출 시도
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return [_validate(item) for item in data if _validate(item)]
        except json.JSONDecodeError:
            pass
    logger.warning("recommendation_parse_failed", preview=text[:200])
    return []


def _validate(item: Any) -> dict | None:
    """LLM JSON 검증 — ticker만 필수. sentiment / macro_verdict / reason은 없으면 None 처리.
    score / recommendation_type은 코드가 산정하므로 LLM 출력에서 무시.
    """
    if not isinstance(item, dict):
        return None
    ticker = item.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    return item
