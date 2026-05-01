"""telegram-notifier 처리 로직 (multi-user fan-out).

흐름:
1. event에서 target_trading_date 파싱
2. PG recommendations 조회 (시장 공통)
3. active users 조회
4. 각 사용자에 대해:
   - 사용자 holdings 조회
   - 메시지 구성:
       * buy_hedge / watch는 모두 포함
       * exit_alert는 사용자 holdings에 있는 ticker만 포함 (없으면 해당 행 스킵)
       * 모든 단계 0건이면 "조건 충족 종목 없음" 메시지 송신
   - 텔레그램 송신, 사용자별 try/except로 격리
5. 결과 메타데이터 반환 (sent_count, failed_count)
"""
from datetime import date
from typing import Iterable

import asyncpg
import structlog
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

from formatter import RecItem, format_message

logger = structlog.get_logger()


async def notify(
    pool: asyncpg.Pool,
    bot: Bot,
    target_trading_date: date,
) -> dict[str, object]:
    """fan-out: active users 전체에 대해 사용자별 메시지 구성 + 송신.

    한 사용자 송신 실패는 다음 사용자에 영향 안 가도록 try/except로 격리.
    RetryAfter는 별도 — 호출자(main.py)가 sleep 후 재처리하도록 그대로 raise.
    """
    async with pool.acquire() as conn:
        rec_rows = await conn.fetch(
            """
            SELECT date, ticker, name, recommendation_type, score,
                   reason_supply, reason_news, reason_macro, estimated_avg_price
            FROM recommendations
            WHERE target_trading_date = $1
            ORDER BY
                CASE recommendation_type
                    WHEN 'buy_hedge' THEN 1
                    WHEN 'watch' THEN 2
                    WHEN 'exit_alert' THEN 3
                    ELSE 4
                END,
                score DESC
            """,
            target_trading_date,
        )

        users = await conn.fetch(
            "SELECT id::text AS id, chat_id FROM users WHERE status = 'active'"
        )

        # holdings에 알려진 종목명을 fallback으로 사용 — recommendations.name이 NULL인 경우
        # (LLM 응답 누락이나 수동 트리거 등). 같은 ticker가 여러 user에 등록돼 있어도 name은 동일.
        name_rows = await conn.fetch(
            "SELECT DISTINCT ON (ticker) ticker, name FROM holdings "
            "WHERE name IS NOT NULL ORDER BY ticker, added_at DESC"
        )

    if not users:
        logger.warning("notify_no_active_users")
        return {"sent_count": 0, "failed_count": 0, "user_count": 0}

    if rec_rows:
        issued_date = max(r["date"] for r in rec_rows)
    else:
        from datetime import timedelta
        issued_date = target_trading_date - timedelta(days=1)

    holdings_name_map: dict[str, str] = {r["ticker"]: r["name"] for r in name_rows}

    base_items = [
        RecItem(
            ticker=r["ticker"],
            name=r["name"] or holdings_name_map.get(r["ticker"]),
            recommendation_type=r["recommendation_type"],
            score=r["score"],
            reason_supply=r["reason_supply"],
            reason_news=r["reason_news"],
            reason_macro=r["reason_macro"],
            estimated_avg_price=r["estimated_avg_price"],
        )
        for r in rec_rows
    ]

    sent: list[dict] = []
    failed: list[dict] = []

    for u in users:
        chat_id = str(u["chat_id"])
        async with pool.acquire() as conn:
            holdings_rows = await conn.fetch(
                "SELECT ticker FROM holdings WHERE user_id = $1::uuid", u["id"]
            )
        user_tickers = {h["ticker"] for h in holdings_rows}

        items_for_user = _filter_for_user(base_items, user_tickers)
        text = format_message(issued_date, target_trading_date, items_for_user)

        try:
            msg = await bot.send_message(
                chat_id=chat_id, text=text, disable_web_page_preview=True
            )
            sent.append({"chat_id": chat_id, "message_id": msg.message_id})
            logger.info(
                "telegram_send_complete", chat_id=chat_id, message_id=msg.message_id
            )
        except RetryAfter:
            # 호출자(main.py)가 sleep + 재시도 처리하도록 raise
            raise
        except TelegramError as exc:
            failed.append({"chat_id": chat_id, "error": str(exc)})
            logger.error("telegram_send_failed", chat_id=chat_id, error=str(exc))

    return {
        "user_count": len(users),
        "sent_count": len(sent),
        "failed_count": len(failed),
        "sent": sent,
        "failed": failed,
    }


def _filter_for_user(items: Iterable[RecItem], user_tickers: set[str]) -> list[RecItem]:
    """exit_alert는 사용자 보유 종목만 포함. buy_hedge/watch는 그대로."""
    result: list[RecItem] = []
    for it in items:
        if it.recommendation_type == "exit_alert" and it.ticker not in user_tickers:
            continue
        result.append(it)
    return result
