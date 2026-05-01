"""worker-data-collector 진입점.

stock.data.requested 수신 → mode에 따라 분기:
  - intraday: process_intraday() → stock.signals.completed
  - premarket: process_premarket() → stock.data.completed (CrewAI 트리거)

mode 누락 시 backward compat — 'premarket'으로 처리하되
target_trading_date 미지정이면 next_business_day(target_date)로 계산.

jobs 테이블 INSERT 책임: 본 워커가 메시지 수신 직후 idempotent INSERT 수행.
- scheduler는 PG 의존성 없으므로 발행만 함
- 후속 단계(crewai recommendations INSERT)의 FK 제약 충족 위해 본 워커가 ownership
"""
import asyncio
import uuid
from datetime import date, timedelta
from typing import Optional

import asyncpg
import structlog

from core.db import close_pool, get_pool
from core.kafka_io import make_consumer, make_producer
from core.logging import setup_logging
from processor import process_intraday, process_premarket

SERVICE_NAME = "worker-data-collector"
TOPIC_IN = "stock.data.requested"
TOPIC_OUT_PREMARKET = "stock.data.completed"
TOPIC_OUT_INTRADAY = "stock.signals.completed"
TOPIC_ERR = "stock.data.failed"
GROUP_ID = "data-collector"
MAX_RETRIES = 3

logger = structlog.get_logger()


async def _ensure_job_row(pool: asyncpg.Pool, job_id: Optional[str]) -> None:
    """jobs.id에 대한 idempotent INSERT.

    - job_id가 UUID 형식이 아니면(수동 트리거의 'manual-...' 등) 조용히 스킵
    - 이미 존재하면 ON CONFLICT DO NOTHING
    - INSERT 실패는 워크플로우를 차단하지 않도록 warning 로그만
    """
    if not job_id:
        return
    try:
        uuid.UUID(job_id)
    except (TypeError, ValueError):
        logger.warning("job_id_not_uuid_skip_insert", job_id=job_id)
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO jobs (id, job_type, status, progress) "
                "VALUES ($1::uuid, 'stock-recommendation', 'in_progress', 10) "
                "ON CONFLICT (id) DO NOTHING",
                job_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("job_row_insert_failed", error=str(exc))


def _next_business_day(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


async def run() -> None:
    setup_logging(SERVICE_NAME)
    pool = await get_pool()
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
            mode = event.get("mode", "premarket")
            structlog.contextvars.bind_contextvars(job_id=job_id, mode=mode)
            try:
                logger.info("worker_received", topic=TOPIC_IN)
                await _ensure_job_row(pool, job_id)
                target_date = date.fromisoformat(event["target_date"])

                if mode == "intraday":
                    result = await process_intraday(pool, target_date)
                    out_topic = TOPIC_OUT_INTRADAY
                    completed_payload = {
                        "job_id": job_id,
                        "target_date": target_date.isoformat(),
                        **result,
                    }
                else:
                    target_trading_date = date.fromisoformat(
                        event.get("target_trading_date")
                        or _next_business_day(target_date).isoformat()
                    )
                    result = await process_premarket(pool, target_date, target_trading_date)
                    out_topic = TOPIC_OUT_PREMARKET
                    completed_payload = {
                        "job_id": job_id,
                        "target_date": target_date.isoformat(),
                        "target_trading_date": target_trading_date.isoformat(),
                        **result,
                    }

                await producer.send_and_wait(out_topic, completed_payload)
                await consumer.commit()
                logger.info("worker_completed", topic_out=out_topic, **result)
            except Exception as exc:  # noqa: BLE001
                logger.error("worker_failed", error=str(exc), exc_info=True)
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
                            "mode": mode,
                            "target_date": event.get("target_date"),
                            "error_code": "PROCESSING_FAILED",
                            "error_message": str(exc),
                            "retry_count": retry_count,
                        },
                    )
                    logger.info("dlq_published", topic=TOPIC_ERR)
                await consumer.commit()
            finally:
                structlog.contextvars.unbind_contextvars("job_id", "mode")
    finally:
        await consumer.stop()
        await producer.stop()
        await close_pool()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run())
