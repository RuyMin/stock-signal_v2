"""텔레그램 추천 알림 메시지 포맷.

큰 그림은 **보유 vs 신규** 두 섹션으로 분리해 사용자가 자기 종목과 신규 추천을 명확히 구분.
종목 줄에 분류(매수 헬지/관망/탈출 경보) 이모지 + 라벨을 함께 표기.

- 헤더: 발행일 → 다음 거래일
- 📌 내 보유 종목 평가 — is_holding=True인 모든 추천 (분류 무관)
- 🔍 신규 추천 — is_holding=False인 buy_hedge/watch (exit_alert는 보유 한정이라 자연 제외)
- 종목당: [icon] 종목명(코드) — 스코어 N, 분류
  - 📊 수급 / 📰 뉴스 / 🌐 매크로 / 💰 매집가(buy_hedge) / ⚠️ 익절손절(exit_alert)
- 추천 0개: "오늘 조건 충족 종목이 없습니다"
- 푸터 면책 문구
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
    is_holding: bool = False


_TYPE_LABEL: dict[str, tuple[str, str]] = {
    "buy_hedge": ("🟢", "매수 헬지"),
    "watch": ("🟡", "관망"),
    "exit_alert": ("🔴", "탈출 경보"),
}
_TYPE_ORDER: dict[str, int] = {"buy_hedge": 1, "watch": 2, "exit_alert": 3}


def _label(ticker: str, name: Optional[str]) -> str:
    return f"{name}({ticker})" if name else ticker


def _block(item: RecItem) -> str:
    icon, type_label = _TYPE_LABEL.get(item.recommendation_type, ("·", item.recommendation_type))
    title = f"{icon} `{_label(item.ticker, item.name)}` — 스코어 {item.score}, {type_label}"
    lines = [title]
    if item.reason_supply:
        lines.append(f"  📊 {item.reason_supply}")
    if item.reason_news:
        lines.append(f"  📰 {item.reason_news}")
    if item.reason_macro:
        lines.append(f"  🌐 {item.reason_macro}")
    if item.recommendation_type == "buy_hedge" and item.estimated_avg_price is not None:
        lines.append(f"  💰 추정 매집가 {int(item.estimated_avg_price):,}원")
    if item.recommendation_type == "exit_alert":
        lines.append("  ⚠️ 익절/손절 검토")
    return "\n".join(lines)


def _sort_for_section(items: list[RecItem]) -> list[RecItem]:
    """섹션 내부 정렬: 분류 우선순위 → 점수 내림차순."""
    return sorted(
        items, key=lambda x: (_TYPE_ORDER.get(x.recommendation_type, 9), -x.score)
    )


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

    holdings = [it for it in items if it.is_holding]
    new_picks = [it for it in items if not it.is_holding]

    parts: list[str] = [header]

    if holdings:
        parts.append(f"\n📌 내 보유 종목 평가 ({len(holdings)}종목)")
        parts.extend(_block(it) for it in _sort_for_section(holdings))

    if new_picks:
        parts.append(f"\n🔍 신규 추천 ({len(new_picks)}종목)")
        parts.extend(_block(it) for it in _sort_for_section(new_picks))

    parts.append("\n⚠️ 최종 판단은 본인이 직접 하세요")
    return "\n".join(parts)
