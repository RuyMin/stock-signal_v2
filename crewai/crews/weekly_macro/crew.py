"""WeeklyMacroReportCrew — 매주 월요일 07:00 KST 트리거.

Sequential 2-task:
  1. MacroSummaryTask  — 매크로 5지표 주간 요약 (Macro Summarizer)
  2. ETFEvaluationTask — ETF별 verdict + 사유 (ETF Evaluator)

on_complete():
- LLM 출력 파싱 (summary + ETF 평가 배열)
- macro_weekly_query 결과는 indicators 스냅샷에서 추출
- weekly_macro_reports 테이블 INSERT (idempotent on week_start)
- per-user payload 구성해서 main.py가 Kafka publish할 출력 반환

페이로드 형식:
{
  "job_id": ...,
  "week_start": "2026-05-04",
  "week_end": "2026-05-10",
  "macro": {"summary": ..., "tone": ..., "indicators": [...]},
  "per_user_etfs": {chat_id: [{ticker, name, verdict, reason}, ...]}
}
"""
import json
import re
from datetime import date as date_type
from datetime import timedelta
from typing import Any

import structlog
from crewai import Process

from core.base_crew import BaseCrew
from core.db import get_pool

from .agents import ETFEvaluatorAgent, MacroSummarizerAgent
from .tasks import ETFEvaluationTask, MacroSummaryTask
from .tools import ETFHoldingsQueryTool

logger = structlog.get_logger()


class WeeklyMacroReportCrew(BaseCrew):
    crew_name = "weekly-macro-report"
    version = "1.0.0"
    process = Process.sequential

    def setup_agents(self) -> list:
        return [
            MacroSummarizerAgent().build(),
            ETFEvaluatorAgent().build(),
        ]

    def setup_tasks(self, agents: list) -> list:
        macro_agent, etf_agent = agents
        macro_task = MacroSummaryTask().build(agent=macro_agent)
        etf_task = ETFEvaluationTask().build(agent=etf_agent, context=[macro_task])
        return [macro_task, etf_task]

    def on_complete(self, raw_result: Any, inputs: dict) -> dict:
        """Crew 결과(2 task 출력) → weekly_macro_reports INSERT + per-user payload.

        raw_result는 마지막 task(ETFEvaluationTask)의 JSON 배열.
        매크로 요약은 첫 task의 출력 — crewai의 task_outputs에서 추출하거나
        ETFEvaluationTask context에서 받아온 정보를 다시 조회.
        간단하게: macro_weekly_query를 on_complete에서 한 번 더 호출해 일관된 스냅샷.
        """
        week_end = date_type.fromisoformat(inputs["target_date"])
        week_start = week_end - timedelta(days=7)

        etf_evaluations = _parse_etf_array(raw_result)
        # ETF 보유자 + chat_id를 holdings 조회로 보강 (raw_result는 ticker만 있음)
        holder_map = _fetch_etf_holders()

        # macro 스냅샷 + summary — crewai task_output을 직접 읽기는 까다로움.
        # 우회: ETF 평가 결과에 매크로 컨텍스트가 녹아있고, summary는 audit 용으로 macro 결과를
        # 한번 더 직접 조회해 indicators만 채움. summary는 raw_result 위쪽 text가 비어있을 수 있어
        # 별도 task 출력 추출 또는 LLM 호출 추가가 필요. 일단 indicators만 채우고 summary는 None 허용.
        # (실제 메시지에 들어가는 summary는 LLM이 ETFEvaluationTask reason에 녹임)
        macro_snapshot = _build_macro_snapshot(week_start, week_end)

        # weekly_macro_reports INSERT (idempotent)
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO weekly_macro_reports
                        (week_start, week_end, job_id, macro_summary, macro_values, etf_evaluations)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (week_start) DO UPDATE SET
                        week_end = EXCLUDED.week_end,
                        job_id = EXCLUDED.job_id,
                        macro_values = EXCLUDED.macro_values,
                        etf_evaluations = EXCLUDED.etf_evaluations,
                        generated_at = NOW()
                    """,
                    (
                        week_start, week_end, self.job_id,
                        None,  # summary는 LLM 출력에서 별도 파싱 (현재 미구현 — TODO Phase 6.x)
                        json.dumps(macro_snapshot),
                        json.dumps(etf_evaluations),
                    ),
                )
            conn.commit()

        # per-user payload — chat_id별 evaluations 매핑
        per_user_etfs: dict[str, list[dict]] = {}
        for ev in etf_evaluations:
            ticker = ev.get("ticker")
            for chat_id in holder_map.get(ticker, []):
                per_user_etfs.setdefault(str(chat_id), []).append(ev)

        return {
            "job_id": self.job_id,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "macro": {
                "indicators": macro_snapshot,
            },
            "per_user_etfs": per_user_etfs,
            "recipient_count": len(per_user_etfs),
            "etf_count": len(etf_evaluations),
        }


_JSON_BLOCK = re.compile(r"\[.*\]", re.DOTALL)


def _parse_etf_array(raw: Any) -> list[dict]:
    """ETFEvaluationTask 결과(JSON 배열) 파싱. 실패 시 빈 리스트."""
    text = str(raw).strip()
    if not text or text == "[]":
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [item for item in data if _validate_eval(item)]
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return [item for item in data if _validate_eval(item)]
        except json.JSONDecodeError:
            pass
    logger.warning("etf_eval_parse_failed", preview=text[:200])
    return []


def _validate_eval(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if not item.get("ticker"):
        return False
    if item.get("verdict") not in {"favorable", "caution", "unfavorable"}:
        return False
    return True


def _fetch_etf_holders() -> dict[str, list[int]]:
    """ticker → [chat_id, ...] 매핑 (active 사용자 ETF 보유)."""
    out: dict[str, list[int]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.ticker, u.chat_id
            FROM holdings h JOIN users u ON u.id = h.user_id
            WHERE h.instrument_type IN ('index_etf', 'sector_etf')
              AND u.status = 'active'
            """
        )
        for ticker, chat_id in cur.fetchall():
            out.setdefault(ticker, []).append(int(chat_id))
    return out


def _build_macro_snapshot(week_start: date_type, week_end: date_type) -> list[dict]:
    """macro_indicators에서 시작/종료값 직접 조회 (Tool 결과와 동일 로직)."""
    indicators_names = ("us10y", "dxy", "wti", "sp500", "gold")
    snapshot = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        for ind in indicators_names:
            cur.execute(
                f"SELECT {ind} FROM macro_indicators "
                f"WHERE date <= %s AND {ind} IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (week_start,),
            )
            row = cur.fetchone()
            start_val = float(row[0]) if row and row[0] is not None else None

            cur.execute(
                f"SELECT {ind} FROM macro_indicators "
                f"WHERE date <= %s AND {ind} IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (week_end,),
            )
            row = cur.fetchone()
            end_val = float(row[0]) if row and row[0] is not None else None

            delta_abs = None
            delta_pct = None
            if start_val is not None and end_val is not None and start_val != 0:
                delta_abs = round(end_val - start_val, 4)
                delta_pct = round((end_val - start_val) / abs(start_val) * 100, 2)

            snapshot.append({
                "name": ind,
                "start": start_val,
                "end": end_val,
                "delta_abs": delta_abs,
                "delta_pct": delta_pct,
            })
    return snapshot
