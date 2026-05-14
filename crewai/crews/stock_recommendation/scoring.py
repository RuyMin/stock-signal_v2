"""추천 점수 산정 — momentum-signals spec 10.1~10.6.

LLM이 score를 매기는 대신 코드가 결정론적으로 계산. LLM은 reason + sentiment
+ macro_verdict만 출력. prompt drift로 score가 비현실적으로 매겨지는 사고
(예: surge 후보가 score 1) 방지.

가중치:
  supply 40% = consecutive 0~20 + momentum 0~20
  news    30%
  macro   20%
  technical 10%
"""
from typing import Optional


def _consecutive_subscore(
    consecutive_buy_days: int,
    agency_net_buy: Optional[int],
    foreign_net_buy: Optional[int],
) -> int:
    """consecutive 0~20. net_buy 합 음수면 강제 0 (매도 우세)."""
    qty = (agency_net_buy or 0) + (foreign_net_buy or 0)
    if qty < 0:
        return 0
    if consecutive_buy_days >= 5:
        return 20
    return {4: 16, 3: 12, 2: 8, 1: 4}.get(consecutive_buy_days, 0)


def _momentum_subscore(
    one_day_net_buy: Optional[int],
    three_day_avg_net_buy: Optional[int],
    volume_ratio: Optional[float],
) -> int:
    """momentum 0~20. surge/acceleration/volume_surge 중 최댓값, 상한 20."""
    surge = 15 if one_day_net_buy is not None and one_day_net_buy >= 10_000_000_000 else 0
    accel = 0
    if (
        one_day_net_buy is not None
        and three_day_avg_net_buy is not None
        and three_day_avg_net_buy > 0
        and one_day_net_buy >= 2 * three_day_avg_net_buy
    ):
        accel = 12
    vol_surge = 10 if volume_ratio is not None and volume_ratio >= 3.0 else 0
    return min(20, max(surge, accel, vol_surge))


def supply_score(
    consecutive_buy_days: int,
    agency_net_buy: Optional[int],
    foreign_net_buy: Optional[int],
    one_day_net_buy: Optional[int],
    three_day_avg_net_buy: Optional[int],
    volume_ratio: Optional[float],
) -> int:
    """수급 컴포넌트 0~40 = consecutive + momentum."""
    return min(
        40,
        _consecutive_subscore(consecutive_buy_days, agency_net_buy, foreign_net_buy)
        + _momentum_subscore(one_day_net_buy, three_day_avg_net_buy, volume_ratio),
    )


_NEWS_MAP = {
    "strongly_positive": 28,
    "positive": 22,
    "neutral": 15,
    "none": 15,
    "negative": 10,
    "strongly_negative": 4,
}


def news_score(sentiment: Optional[str]) -> int:
    """뉴스 컴포넌트 0~30. LLM이 평가한 sentiment label 매핑.
    미인식 라벨이나 None은 neutral(15)로 처리.
    """
    return _NEWS_MAP.get((sentiment or "neutral").lower(), 15)


_MACRO_MAP = {
    "favorable": 18,
    "neutral": 10,
    "unknown": 10,
    "unfavorable": 4,
}


def macro_score(verdict: Optional[str]) -> int:
    """매크로 컴포넌트 0~20. LLM의 글로벌 매크로 verdict 매핑."""
    return _MACRO_MAP.get((verdict or "unknown").lower(), 10)


def technical_score(
    rsi: Optional[float],
    ma_alignment: Optional[str],
    bollinger_position: Optional[float],
    trading_value: Optional[int],
) -> int:
    """기술적 컴포넌트 0~10. RSI + MA + BB + 거래대금 - 유동성 페널티, [0,10] 캡."""
    if rsi is None:
        rsi_s = 0
    elif rsi < 30:
        rsi_s = 8
    elif rsi <= 70:
        rsi_s = 5
    else:
        rsi_s = 2

    ma_s = {"bullish": 3, "neutral": 1, "bearish": 0}.get(ma_alignment or "", 0)

    if bollinger_position is None:
        bb_s = 0
    elif bollinger_position < 0.2:
        bb_s = 2
    elif bollinger_position <= 0.7:
        bb_s = 1
    else:
        bb_s = 0

    tv_s = 1 if trading_value is not None and trading_value >= 10_000_000_000 else 0

    penalty = 5 if trading_value is not None and trading_value < 5_000_000_000 else 0

    return max(0, min(10, rsi_s + ma_s + bb_s + tv_s - penalty))


def total_score(
    signal_row: Optional[dict],
    sentiment: Optional[str],
    macro_verdict: Optional[str],
) -> tuple[int, dict]:
    """4컴포넌트 합산, 0~100.

    signal_row가 None(signals 미수집 종목 — KIS 상위 30위 밖, ETF/저거래주 등)이면
    supply=0, technical=0. news + macro만으로 산정되어 자연스럽게 낮게 나옴.

    Returns:
        (total, breakdown_dict) — breakdown은 진단/로깅용 컴포넌트 분해.
    """
    if signal_row:
        s = supply_score(
            signal_row.get("consecutive_buy_days") or 0,
            signal_row.get("agency_net_buy"),
            signal_row.get("foreign_net_buy"),
            signal_row.get("one_day_net_buy"),
            signal_row.get("three_day_avg_net_buy"),
            signal_row.get("volume_ratio"),
        )
        t = technical_score(
            signal_row.get("rsi"),
            signal_row.get("ma_alignment"),
            signal_row.get("bollinger_position"),
            signal_row.get("trading_value"),
        )
    else:
        s = 0
        t = 0
    n = news_score(sentiment)
    m = macro_score(macro_verdict)
    return min(100, max(0, s + n + m + t)), {
        "supply": s,
        "news": n,
        "macro": m,
        "technical": t,
    }
