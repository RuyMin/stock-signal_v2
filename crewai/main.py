"""crewai 진입점.

두 종류의 사이클을 별도 컨슈머로 동시 처리:
- stock.data.completed       → StockRecommendationCrew  (daily 추천)
- stock.weekly_macro.requested → WeeklyMacroReportCrew  (주간 매크로 리포트)

CrewAI Crew는 동기. asyncio.to_thread로 호출하여 메인 이벤트 루프 차단 방지.
"""
import asyncio

import structlog

from core.db import close_pool
from core.kafka_io import make_consumer, make_producer
from core.logging import setup_logging
from crews.stock_recommendation import StockRecommendationCrew
from crews.weekly_macro import WeeklyMacroReportCrew

SERVICE_NAME = "crewai"

# Daily 추천 사이클
TOPIC_IN = "stock.data.completed"
TOPIC_OUT = "stock.recommendation.completed"
TOPIC_ERR = "stock.recommendation.failed"
GROUP_ID = "crewai-stock-recommendation"

# Weekly 매크로 사이클
TOPIC_IN_WEEKLY = "stock.weekly_macro.requested"
TOPIC_OUT_WEEKLY = "stock.weekly_macro.report.completed"
TOPIC_ERR_WEEKLY = "stock.weekly_macro.report.failed"
GROUP_ID_WEEKLY = "crewai-weekly-macro"

MAX_RETRIES = 3

logger = structlog.get_logger()


async def _loop_daily(consumer, producer) -> None:
    """stock.data.completed 처리 루프 — daily 추천 사이클."""
    async for msg in consumer:
        event = msg.value
        if not isinstance(event, dict):
            logger.warning(
                "poison_message_skipped",
                topic=TOPIC_IN,
                offset=msg.offset,
                value_type=type(event).__name__,
            )
            await consumer.commit()
            continue
        job_id = event.get("job_id")
        structlog.contextvars.bind_contextvars(job_id=job_id)
        try:
            logger.info("crew_received", topic=TOPIC_IN)
            gap_days = int(event.get("holiday_gap_days") or 0)
            holidays_in_gap = event.get("holidays_in_gap") or []
            if gap_days <= 0 or not holidays_in_gap:
                holiday_gap_text = (
                    "직전 거래일과 다음 거래일이 연속(휴장일 갭 없음)."
                )
            else:
                items = ", ".join(
                    f"{h['date']}({h['reason']})" for h in holidays_in_gap
                )
                holiday_gap_text = (
                    f"직전 거래일 ↔ 다음 거래일 사이 {gap_days}일 휴장: {items}. "
                    "이 기간 동안 발생한 글로벌 뉴스/매크로 변화는 다음 거래일에 큰 영향을 미칠 수 있다."
                )
            inputs = {
                "target_date": event["target_date"],
                "target_trading_date": event["target_trading_date"],
                "holiday_gap_text": holiday_gap_text,
            }
            crew = StockRecommendationCrew(job_id=job_id)
            completed = await asyncio.to_thread(crew.kickoff, inputs)

            await producer.send_and_wait(TOPIC_OUT, completed)
            await consumer.commit()
            logger.info(
                "worker_completed",
                recommendation_count=completed.get("recommendation_count"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("crew_failed", error=str(exc), exc_info=True)
            retry_count = int(event.get("retry_count", 0))
            if retry_count < MAX_RETRIES:
                await producer.send_and_wait(
                    TOPIC_IN, {**event, "retry_count": retry_count + 1}
                )
                logger.info("retry_scheduled", retry_count=retry_count + 1)
            else:
                await producer.send_and_wait(
                    TOPIC_ERR,
                    {
                        "job_id": job_id,
                        "error_code": "CREW_FAILED",
                        "error_message": str(exc),
                        "retry_count": retry_count,
                    },
                )
                logger.info("dlq_published", topic=TOPIC_ERR)
            await consumer.commit()
        finally:
            structlog.contextvars.unbind_contextvars("job_id")


async def _loop_weekly(consumer, producer) -> None:
    """stock.weekly_macro.requested 처리 루프 — 주간 매크로 리포트."""
    async for msg in consumer:
        event = msg.value
        if not isinstance(event, dict):
            logger.warning(
                "poison_message_skipped",
                topic=TOPIC_IN_WEEKLY,
                offset=msg.offset,
                value_type=type(event).__name__,
            )
            await consumer.commit()
            continue
        job_id = event.get("job_id")
        structlog.contextvars.bind_contextvars(job_id=job_id)
        try:
            logger.info("weekly_macro_received", topic=TOPIC_IN_WEEKLY)
            inputs = {
                "target_date": event["target_date"],
                # MacroSummaryTask description의 {week_start} / {week_end} placeholder.
                # target_date를 week_end로, 7일 전을 week_start로 prompt에 노출.
                "week_end": event["target_date"],
                "week_start": _week_start(event["target_date"]),
            }
            crew = WeeklyMacroReportCrew(job_id=job_id)
            completed = await asyncio.to_thread(crew.kickoff, inputs)

            await producer.send_and_wait(TOPIC_OUT_WEEKLY, completed)
            await consumer.commit()
            logger.info(
                "weekly_macro_completed",
                recipient_count=completed.get("recipient_count"),
                etf_count=completed.get("etf_count"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("weekly_macro_failed", error=str(exc), exc_info=True)
            await producer.send_and_wait(
                TOPIC_ERR_WEEKLY,
                {
                    "job_id": job_id,
                    "error_code": "WEEKLY_MACRO_FAILED",
                    "error_message": str(exc),
                },
            )
            logger.info("dlq_published", topic=TOPIC_ERR_WEEKLY)
            await consumer.commit()
        finally:
            structlog.contextvars.unbind_contextvars("job_id")


def _week_start(target_date_iso: str) -> str:
    """target_date 기준 7일 전 (week_start) ISO 문자열."""
    from datetime import date, timedelta
    d = date.fromisoformat(target_date_iso)
    return (d - timedelta(days=7)).isoformat()


async def run() -> None:
    setup_logging(SERVICE_NAME)
    daily_consumer = make_consumer(TOPIC_IN, GROUP_ID)
    weekly_consumer = make_consumer(TOPIC_IN_WEEKLY, GROUP_ID_WEEKLY)
    producer = make_producer()

    await daily_consumer.start()
    await weekly_consumer.start()
    await producer.start()
    logger.info(
        "startup_complete",
        topic_in_daily=TOPIC_IN,
        topic_in_weekly=TOPIC_IN_WEEKLY,
    )

    try:
        await asyncio.gather(
            _loop_daily(daily_consumer, producer),
            _loop_weekly(weekly_consumer, producer),
        )
    finally:
        await daily_consumer.stop()
        await weekly_consumer.stop()
        await producer.stop()
        close_pool()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run())
