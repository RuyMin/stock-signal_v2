"""ETF ticker → 추종 지수 매핑.

신규 ETF 추가 시 이 파일에 한 줄 추가. 향후 DB 테이블로 분리 가능.
값: 'us10y' | 'dxy' | 'wti' | 'sp500' | 'gold' | 'kospi' | 'kosdaq' | 'nasdaq' | None
(macro_indicators 테이블의 5지표 + 한국 지수)
"""
from typing import Optional

# 한국 시장에서 자주 거래되는 주요 ETF.
# 점진 확장 — 매핑 안 된 ETF는 일반 매크로 톤 적용 (fallback).
TICKER_TO_INDEX: dict[str, str] = {
    # 미국 S&P500
    "379800": "sp500",   # KODEX 미국S&P500
    "360200": "sp500",   # ACE 미국S&P500
    # 미국 NASDAQ
    "133690": "nasdaq",  # TIGER 미국나스닥100
    "381180": "nasdaq",  # TIGER 미국필라델피아반도체나스닥
    # 한국 KOSPI
    "069500": "kospi",   # KODEX 200
    "102110": "kospi",   # TIGER 200
    # 한국 KOSDAQ
    "229200": "kosdaq",  # KODEX 코스닥150
    # 추가는 여기에 한 줄씩
}


def tracking_index(ticker: str) -> Optional[str]:
    """ETF ticker → 추종 지수 ID. 매핑 없으면 None."""
    return TICKER_TO_INDEX.get(ticker)
