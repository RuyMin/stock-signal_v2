"""주간 매크로 리포트 메시지 송신.

stock.weekly_macro.report.completed 토픽 페이로드를 받아 per_user_etfs에 매핑된
사용자에게만 텔레그램으로 송신. ETF 미보유 사용자는 자동 skip.

페이로드 구조:
{
  "job_id": "...",
  "week_start": "2026-05-04",
  "week_end": "2026-05-11",
  "macro": {"indicators": [{name, start, end, delta_abs, delta_pct}, ...]},
  "per_user_etfs": {chat_id: [{ticker, name, verdict, reason}, ...]}
}
"""
from typing import Any

import structlog
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

logger = structlog.get_logger()

_INDICATOR_LABELS: dict[str, str] = {
    "us10y": "미 10년물",
    "dxy": "DXY",
    "wti": "WTI",
    "sp500": "S&P 500",
    "gold": "금",
}

_VERDICT_EMOJI: dict[str, str] = {
    "favorable": "🟢",
    "caution": "🟡",
    "unfavorable": "🔴",
}


def _fmt_indicator(ind: dict) -> str:
    name = _INDICATOR_LABELS.get(ind["name"], ind["name"])
    start = ind.get("start")
    end = ind.get("end")
    delta_abs = ind.get("delta_abs")
    delta_pct = ind.get("delta_pct")
    if start is None or end is None:
        return f"• {name}: 데이터 부족"
    sign_abs = "+" if delta_abs is not None and delta_abs >= 0 else ""
    sign_pct = "+" if delta_pct is not None and delta_pct >= 0 else ""
    return (
        f"• {name}: {start:g} → {end:g} "
        f"({sign_abs}{delta_abs:g}, {sign_pct}{delta_pct:g}%)"
    )


def _fmt_etf_eval(ev: dict) -> str:
    emoji = _VERDICT_EMOJI.get(ev.get("verdict") or "", "⚪")
    name = ev.get("name") or "(이름 미확인)"
    ticker = ev.get("ticker", "")
    verdict = ev.get("verdict") or "unknown"
    reason = ev.get("reason") or "사유 없음"
    return f"{emoji} `{name}({ticker})` — {verdict}\n  → {reason}"


def format_weekly_message(event: dict[str, Any], etfs: list[dict]) -> str:
    """사용자별 주간 매크로 리포트 메시지 본문 생성 (Markdown)."""
    week_start = event.get("week_start", "")
    week_end = event.get("week_end", "")
    indicators = (event.get("macro") or {}).get("indicators") or []

    lines: list[str] = []
    lines.append(f"📅 주간 매크로 리포트 ({week_start} ~ {week_end})")
    lines.append("")
    lines.append("📊 매크로 5지표")
    for ind in indicators:
        lines.append(_fmt_indicator(ind))
    lines.append("")
    lines.append("📌 내 ETF 평가")
    for ev in etfs:
        lines.append(_fmt_etf_eval(ev))
    lines.append("")
    lines.append("⚠️ 최종 판단은 본인이 직접 하세요")
    return "\n".join(lines)


async def notify_weekly(
    bot: Bot,
    event: dict[str, Any],
) -> dict[str, object]:
    """주간 매크로 리포트 fan-out — per_user_etfs 키만 발송.

    실패는 per-user try/except로 격리. RetryAfter는 한 사용자에서 발생해도 같음 처리 — sleep.
    """
    per_user = event.get("per_user_etfs") or {}
    if not per_user:
        logger.info("weekly_macro_no_etf_holders", job_id=event.get("job_id"))
        return {
            "sent_count": 0,
            "failed_count": 0,
            "recipient_count": 0,
        }

    sent: list[str] = []
    failed: list[str] = []
    for chat_id_str, etfs in per_user.items():
        if not etfs:
            continue
        text = format_weekly_message(event, etfs)
        try:
            await bot.send_message(
                chat_id=int(chat_id_str),
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            sent.append(chat_id_str)
            logger.info(
                "weekly_macro_per_user_done",
                chat_id=chat_id_str,
                etf_count=len(etfs),
            )
        except RetryAfter:
            # rate limit는 호출자에서 처리 — re-raise해서 메인 루프가 sleep + 미커밋으로 재처리
            raise
        except (TelegramError, Exception) as exc:  # noqa: BLE001
            failed.append(chat_id_str)
            logger.error(
                "weekly_macro_send_failed",
                chat_id=chat_id_str,
                error=str(exc),
            )
    return {
        "sent_count": len(sent),
        "failed_count": len(failed),
        "recipient_count": len(per_user),
    }
