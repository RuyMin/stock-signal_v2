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
        """Synthesizer 출력(JSON 배열) 파싱 → recommendations INSERT.

        후처리 강제 (LLM 분류 룰 일탈 방지):
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
                    # 보유 종목 합집합 조회 (분류 후처리용 — score<50 보유는 exit_alert 강제)
                    cur.execute("SELECT DISTINCT ticker FROM holdings")
                    holdings_set = {r[0] for r in cur.fetchall()}

                    for it in items:
                        ticker = it["ticker"]
                        score = int(it["score"])
                        is_holding = ticker in holdings_set
                        llm_type = it.get("recommendation_type")

                        # name 보강: LLM 명시 우선 → KIS API 즉시 조회 → NULL fallback
                        # (notifier가 holdings.name으로 추가 fallback 시도)
                        rec_name = (it.get("name") or "").strip() or None
                        if rec_name is None:
                            rec_name = kis_api.fetch_ticker_name(ticker)

                        # score 기반 type 강제 재분류
                        if score >= 70:
                            rec_type = "buy_hedge"
                        elif score >= 50:
                            rec_type = "watch"
                        elif is_holding:
                            rec_type = "exit_alert"
                        else:
                            # 신규 후보 score<50 → 시장 추천 가치 없음, 제외
                            logger.info(
                                "skipped_low_score_new_candidate",
                                ticker=ticker, score=score,
                            )
                            continue

                        # LLM 분류와 강제 재분류 차이 모니터링
                        if llm_type != rec_type:
                            logger.info(
                                "type_reclassified",
                                ticker=ticker, score=score,
                                llm_type=llm_type, enforced_type=rec_type,
                                is_holding=is_holding,
                            )

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
                                it.get("estimated_avg_price"),
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
    if not isinstance(item, dict):
        return None
    required = {"ticker", "recommendation_type", "score"}
    if not required.issubset(item.keys()):
        return None
    if item["recommendation_type"] not in {"buy_hedge", "watch", "exit_alert"}:
        return None
    try:
        score = int(item["score"])
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        return None
    return item
