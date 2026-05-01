"""텔레그램 추천 알림 메시지 포맷.

PRD §"텔레그램 알림 형식" 준수:
- 헤더: 발행일 → 다음 거래일
- 단계별 종목 (매수 헬지 / 관망 / 탈출 경보)
- 종목당: 종목명(코드) — 스코어, 수급, 뉴스, 매크로, (매수 헬지: 추정 매집가)
- 추천 0개: "오늘 조건 충족 종목이 없습니다"
- 하단 면책 문구: "최종 판단은 본인이 직접 하세요"
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(slots=True)
class RecItem:
    ticker: str
    name: Optional[str]
    recommendation_type: str  # buy_hedge | watch | exit_alert
    score: int
    reason_supply: Optional[str]
    reason_news: Optional[str]
    reason_macro: Optional[str]
    estimated_avg_price: Optional[Decimal]


def _label(ticker: str, name: Optional[str]) -> str:
    return f"{name}({ticker})" if name else ticker


def _block(item: RecItem, exit_alert: bool = False) -> str:
    lines = [f"`{_label(item.ticker, item.name)}` — 스코어 {item.score}"]
    if item.reason_supply:
        lines.append(f"  📊 {item.reason_supply}")
    if item.reason_news:
        lines.append(f"  📰 {item.reason_news}")
    if item.reason_macro:
        lines.append(f"  🌐 {item.reason_macro}")
    if item.recommendation_type == "buy_hedge" and item.estimated_avg_price is not None:
        lines.append(f"  💰 추정 매집가 {int(item.estimated_avg_price):,}원")
    if exit_alert:
        lines.append("  ⚠️ 익절/손절 검토")
    return "\n".join(lines)


def format_message(
    issued_date: date,
    target_trading_date: date,
    items: list[RecItem],
) -> str:
    header = (
        f"🤖 [AI 수급 종목 추천] {issued_date.isoformat()} "
        f"→ 다음 거래일 ({target_trading_date.isoformat()})"
    )
    if not items:
        return (
            f"{header}\n\n"
            "오늘 조건 충족 종목이 없습니다.\n\n"
            "⚠️ 최종 판단은 본인이 직접 하세요"
        )

    by_type: dict[str, list[RecItem]] = {"buy_hedge": [], "watch": [], "exit_alert": []}
    for it in items:
        by_type.setdefault(it.recommendation_type, []).append(it)

    parts: list[str] = [header]

    if by_type["buy_hedge"]:
        parts.append(f"\n🟢 매수 헬지 ({len(by_type['buy_hedge'])}종목)")
        parts.extend(_block(it) for it in by_type["buy_hedge"])
    if by_type["watch"]:
        parts.append(f"\n🟡 관망 ({len(by_type['watch'])}종목)")
        parts.extend(_block(it) for it in by_type["watch"])
    if by_type["exit_alert"]:
        parts.append(f"\n🔴 탈출 경보 ({len(by_type['exit_alert'])}종목 — 보유)")
        parts.extend(_block(it, exit_alert=True) for it in by_type["exit_alert"])

    parts.append("\n⚠️ 최종 판단은 본인이 직접 하세요")
    return "\n".join(parts)
