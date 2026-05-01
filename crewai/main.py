"""crewai 진입점.

stock.data.completed 수신 → StockRecommendationCrew.kickoff(inputs) 동기 호출
→ stock.recommendation.completed (또는 .failed) 발행.

CrewAI Crew는 동기. asyncio.to_thread로 호출하여 메인 이벤트 루프 차단 방지.
"""
import asyncio

import structlog

from core.db import close_pool
from core.kafka_io import make_consumer, make_producer
from core.logging import setup_logging
from crews.stock_recommendation import StockRecommendationCrew

SERVICE_NAME = "crewai"
TOPIC_IN = "stock.data.completed"
TOPIC_OUT = "stock.recommendation.completed"
TOPIC_ERR = "stock.recommendation.failed"
GROUP_ID = "crewai-stock-recommendation"
MAX_RETRIES = 3

logger = structlog.get_logger()


async def run() -> None:
    setup_logging(SERVICE_NAME)
    consumer = make_consumer(TOPIC_IN, GROUP_ID)
    producer = make_producer()

    await consumer.start()
    await producer.start()
    logger.info("startup_complete", topic_in=TOPIC_IN)

    try:
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
                inputs = {
                    "target_date": event["target_date"],
                    "target_trading_date": event["target_trading_date"],
                }
                crew = StockRecommendationCrew(job_id=job_id)
                # CrewAI는 동기 — 별도 스레드에서 실행
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
    finally:
        await consumer.stop()
        await producer.stop()
        close_pool()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run())
