"""worker-telegram-notifier 진입점.

stock.recommendation.completed 수신 → recommendations 조회 + 텔레그램 송신
→ stock.notify.completed (또는 .failed) 발행.
"""
import asyncio
import os
from datetime import date

import structlog
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

from core.db import close_pool, get_pool
from core.kafka_io import make_consumer, make_producer
from core.logging import setup_logging
from processor import notify

SERVICE_NAME = "worker-telegram-notifier"
TOPIC_IN = "stock.recommendation.completed"
TOPIC_OUT = "stock.notify.completed"
TOPIC_ERR = "stock.notify.failed"
GROUP_ID = "telegram-notifier"
MAX_RETRIES = 3

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# multi-user 전환(2026-04-30): TELEGRAM_AUTHORIZED_CHAT_ID는 더 이상 직접 참조하지 않음.
# notifier.processor가 PG users 테이블의 active 사용자 전체에 fan-out한다.

logger = structlog.get_logger()


async def run() -> None:
    setup_logging(SERVICE_NAME)
    pool = await get_pool()
    consumer = make_consumer(TOPIC_IN, GROUP_ID)
    producer = make_producer()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

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
                logger.info("worker_received", topic=TOPIC_IN)
                target_trading_date = date.fromisoformat(event["target_trading_date"])
                result = await notify(pool, bot, target_trading_date)

                completed_payload = {
                    "job_id": job_id,
                    **result,
                }
                await producer.send_and_wait(TOPIC_OUT, completed_payload)
                await consumer.commit()
                logger.info("worker_completed")
            except RetryAfter as exc:
                # 텔레그램 rate limit — retry-after만큼 대기 후 재시도
                logger.warning("telegram_rate_limited", retry_after=exc.retry_after)
                await asyncio.sleep(float(exc.retry_after))
                # offset commit 안 함 → consumer가 같은 메시지 재수신
            except (TelegramError, Exception) as exc:  # noqa: BLE001
                logger.error("telegram_send_failed", error=str(exc), exc_info=True)
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
                            "error_code": "TELEGRAM_SEND_FAILED",
                            "error_message": str(exc),
                        },
                    )
                    logger.info("dlq_published", topic=TOPIC_ERR)
                await consumer.commit()
            finally:
                structlog.contextvars.unbind_contextvars("job_id")
    finally:
        await consumer.stop()
        await producer.stop()
        await close_pool()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run())
