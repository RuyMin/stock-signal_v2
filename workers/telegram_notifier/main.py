"""worker-telegram-notifier 진입점.

두 토픽 동시 컨슈머:
- stock.recommendation.completed         → daily 추천 fan-out
- stock.weekly_macro.report.completed   → 주간 매크로 리포트 fan-out (ETF 보유자만)
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
from weekly_processor import notify_weekly

SERVICE_NAME = "worker-telegram-notifier"

# Daily 추천
TOPIC_IN = "stock.recommendation.completed"
TOPIC_OUT = "stock.notify.completed"
TOPIC_ERR = "stock.notify.failed"
GROUP_ID = "telegram-notifier"

# Weekly macro
TOPIC_IN_WEEKLY = "stock.weekly_macro.report.completed"
TOPIC_OUT_WEEKLY = "stock.weekly_macro.notify.completed"
TOPIC_ERR_WEEKLY = "stock.weekly_macro.notify.failed"
GROUP_ID_WEEKLY = "telegram-notifier-weekly"

MAX_RETRIES = 3

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

logger = structlog.get_logger()


async def _loop_daily(pool, consumer, producer, bot: Bot) -> None:
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
            logger.warning("telegram_rate_limited", retry_after=exc.retry_after)
            await asyncio.sleep(float(exc.retry_after))
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


async def _loop_weekly(consumer, producer, bot: Bot) -> None:
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
            logger.info("weekly_macro_notify_received", topic=TOPIC_IN_WEEKLY)
            result = await notify_weekly(bot, event)

            await producer.send_and_wait(
                TOPIC_OUT_WEEKLY, {"job_id": job_id, **result}
            )
            await consumer.commit()
            logger.info("weekly_macro_notify_completed", **result)
        except RetryAfter as exc:
            logger.warning("telegram_rate_limited", retry_after=exc.retry_after)
            await asyncio.sleep(float(exc.retry_after))
        except (TelegramError, Exception) as exc:  # noqa: BLE001
            logger.error("weekly_macro_notify_failed", error=str(exc), exc_info=True)
            await producer.send_and_wait(
                TOPIC_ERR_WEEKLY,
                {
                    "job_id": job_id,
                    "error_code": "WEEKLY_MACRO_NOTIFY_FAILED",
                    "error_message": str(exc),
                },
            )
            logger.info("dlq_published", topic=TOPIC_ERR_WEEKLY)
            await consumer.commit()
        finally:
            structlog.contextvars.unbind_contextvars("job_id")


async def run() -> None:
    setup_logging(SERVICE_NAME)
    pool = await get_pool()
    daily_consumer = make_consumer(TOPIC_IN, GROUP_ID)
    weekly_consumer = make_consumer(TOPIC_IN_WEEKLY, GROUP_ID_WEEKLY)
    producer = make_producer()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

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
            _loop_daily(pool, daily_consumer, producer, bot),
            _loop_weekly(weekly_consumer, producer, bot),
        )
    finally:
        await daily_consumer.stop()
        await weekly_consumer.stop()
        await producer.stop()
        await close_pool()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run())
