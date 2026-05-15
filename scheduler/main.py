"""scheduler — 2-cron으로 stock.data.requested 발행.

- intraday (KST 16:30 default): mode='intraday', signals 수집만 트리거
- premarket (KST 06:30 default): mode='premarket', macro/news/추천 트리거

휴장일(주말 + 한국 공휴일)에는 발행 스킵.
holidays.KR()로 한국 공휴일 판정 — KRX 임시 휴장(예: 마지막 거래일)은
완벽 매핑 안 됨. 운영 1주 후 보정 필요.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import structlog
from aiokafka import AIOKafkaProducer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

KAFKA_BOOTSTRAP_SERVERS = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
TOPIC_OUT = "stock.data.requested"
TOPIC_OUT_WEEKLY = "stock.weekly_macro.requested"


def _env_int(name: str, default: int) -> int:
    """빈 문자열도 default로 폴백 (docker compose가 unset env를 ""로 전달하기 때문)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


INTRADAY_HOUR = _env_int("SCHEDULE_INTRADAY_HOUR", 16)
INTRADAY_MINUTE = _env_int("SCHEDULE_INTRADAY_MINUTE", 30)
PREMARKET_HOUR = _env_int("SCHEDULE_PREMARKET_HOUR", 6)
PREMARKET_MINUTE = _env_int("SCHEDULE_PREMARKET_MINUTE", 30)
WEEKLY_MACRO_HOUR = _env_int("SCHEDULE_WEEKLY_MACRO_HOUR", 7)
WEEKLY_MACRO_MINUTE = _env_int("SCHEDULE_WEEKLY_MACRO_MINUTE", 0)

TZ_KST = ZoneInfo("Asia/Seoul")
SERVICE_NAME = "scheduler"


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=SERVICE_NAME)


logger = structlog.get_logger()
producer: AIOKafkaProducer | None = None


def is_market_open(d: date) -> bool:
    if d.weekday() >= 5:  # Sat, Sun
        return False
    kr_holidays = holidays.KR(years=d.year)
    return d not in kr_holidays


def previous_business_day(d: date) -> date:
    p = d - timedelta(days=1)
    while not is_market_open(p):
        p -= timedelta(days=1)
    return p


def holidays_between(start: date, end: date) -> list[dict]:
    """start와 end 사이(둘 다 제외)의 휴장 일자 목록. start < end 가정.

    각 원소: {"date": "YYYY-MM-DD", "reason": "주말 (토요일)" | "어린이날" 등}.
    LLM 컨텍스트로 휴장일 갭 동안 글로벌 뉴스 변화를 인식하도록 함.
    """
    out: list[dict] = []
    if end <= start:
        return out
    try:
        kr_hols = holidays.KR(years=range(start.year, end.year + 1), language="ko")
    except TypeError:
        # 구 버전 holidays 라이브러리는 language 인자 미지원 — fallback
        kr_hols = holidays.KR(years=range(start.year, end.year + 1))
    cur = start + timedelta(days=1)
    while cur < end:
        if cur.weekday() == 5:
            out.append({"date": cur.isoformat(), "reason": "주말 (토요일)"})
        elif cur.weekday() == 6:
            out.append({"date": cur.isoformat(), "reason": "주말 (일요일)"})
        elif cur in kr_hols:
            out.append({"date": cur.isoformat(), "reason": kr_hols.get(cur, "공휴일")})
        cur += timedelta(days=1)
    return out


async def _publish(payload: dict) -> None:
    assert producer is not None
    await producer.send_and_wait(TOPIC_OUT, json.dumps(payload, default=str).encode("utf-8"))
    logger.info("trigger_published", topic=TOPIC_OUT, mode=payload.get("mode"))


async def _publish_to(topic: str, payload: dict) -> None:
    """임의 토픽 발행 — weekly_macro 등 신규 토픽용."""
    assert producer is not None
    await producer.send_and_wait(topic, json.dumps(payload, default=str).encode("utf-8"))
    logger.info("trigger_published", topic=topic, mode=payload.get("mode"))


async def trigger_intraday() -> None:
    """KST 16:30 — D일이 거래일이면 signals 수집 트리거."""
    today_kst = datetime.now(TZ_KST).date()
    job_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(job_id=job_id)
    try:
        logger.info(
            "scheduler_triggered", mode="intraday", target_date=today_kst.isoformat()
        )
        if not is_market_open(today_kst):
            logger.info(
                "scheduler_skipped_holiday",
                mode="intraday",
                target_date=today_kst.isoformat(),
            )
            return
        await _publish(
            {
                "job_id": job_id,
                "mode": "intraday",
                "target_date": today_kst.isoformat(),
                "triggered_at": datetime.utcnow().isoformat(),
            }
        )
    finally:
        structlog.contextvars.unbind_contextvars("job_id")


async def trigger_premarket() -> None:
    """KST 06:30 — 오늘(D+1)이 거래일이면 매크로+뉴스+추천 트리거.

    signals 데이터 기준일은 직전 한국 거래일(어제 또는 그 전)이며,
    target_trading_date는 오늘(=장 시작 예정일)로 설정한다.
    """
    today_kst = datetime.now(TZ_KST).date()
    job_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(job_id=job_id)
    try:
        logger.info(
            "scheduler_triggered",
            mode="premarket",
            target_trading_date=today_kst.isoformat(),
        )
        if not is_market_open(today_kst):
            logger.info(
                "scheduler_skipped_holiday",
                mode="premarket",
                target_trading_date=today_kst.isoformat(),
            )
            return
        signal_date = previous_business_day(today_kst)
        holidays_in_gap = holidays_between(signal_date, today_kst)
        await _publish(
            {
                "job_id": job_id,
                "mode": "premarket",
                "target_date": signal_date.isoformat(),
                "target_trading_date": today_kst.isoformat(),
                "holiday_gap_days": (today_kst - signal_date).days - 1,
                "holidays_in_gap": holidays_in_gap,
                "triggered_at": datetime.utcnow().isoformat(),
            }
        )
    finally:
        structlog.contextvars.unbind_contextvars("job_id")


async def trigger_weekly_macro() -> None:
    """매주 월요일 07:00 KST. 월요일이 휴장이면 다음 거래일로 시프트.

    etf-and-weekly-macro spec — ETF 보유자에게 주간 매크로 요약 + 우호도 메시지.
    오늘이 휴장이면 그 주 안에서 다음 거래일까지 최대 5일 탐색 후 publish.
    """
    today_kst = datetime.now(TZ_KST).date()
    target = today_kst
    days_searched = 0
    while not is_market_open(target):
        target += timedelta(days=1)
        days_searched += 1
        if days_searched > 5:
            logger.warning(
                "weekly_macro_skip_no_trading_day_in_week",
                today=today_kst.isoformat(),
            )
            return
    job_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(job_id=job_id)
    try:
        logger.info(
            "weekly_macro_triggered",
            today=today_kst.isoformat(),
            target_date=target.isoformat(),
            shifted=target != today_kst,
        )
        await _publish_to(
            TOPIC_OUT_WEEKLY,
            {
                "job_id": job_id,
                "mode": "weekly_macro",
                "target_date": target.isoformat(),
                "triggered_at": datetime.utcnow().isoformat(),
            },
        )
    finally:
        structlog.contextvars.unbind_contextvars("job_id")


async def main() -> None:
    setup_logging()
    global producer
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
    await producer.start()
    logger.info(
        "startup_complete",
        intraday=f"{INTRADAY_HOUR:02d}:{INTRADAY_MINUTE:02d} KST",
        premarket=f"{PREMARKET_HOUR:02d}:{PREMARKET_MINUTE:02d} KST",
        weekly_macro=f"Mon {WEEKLY_MACRO_HOUR:02d}:{WEEKLY_MACRO_MINUTE:02d} KST",
    )

    scheduler = AsyncIOScheduler(timezone=TZ_KST)
    scheduler.add_job(
        trigger_intraday,
        CronTrigger(hour=INTRADAY_HOUR, minute=INTRADAY_MINUTE, timezone=TZ_KST),
        id="daily-intraday-trigger",
        replace_existing=True,
    )
    scheduler.add_job(
        trigger_premarket,
        CronTrigger(hour=PREMARKET_HOUR, minute=PREMARKET_MINUTE, timezone=TZ_KST),
        id="daily-premarket-trigger",
        replace_existing=True,
    )
    scheduler.add_job(
        trigger_weekly_macro,
        CronTrigger(
            day_of_week="mon",
            hour=WEEKLY_MACRO_HOUR,
            minute=WEEKLY_MACRO_MINUTE,
            timezone=TZ_KST,
        ),
        id="weekly-macro-trigger",
        replace_existing=True,
    )
    scheduler.start()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
        await producer.stop()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
