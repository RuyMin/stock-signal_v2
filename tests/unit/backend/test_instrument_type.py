"""infer_instrument_type 단위 테스트 — 순수 함수."""
from __future__ import annotations

import pytest

from schemas.holdings import infer_instrument_type  # type: ignore[import-not-found]


class TestInferInstrumentType:
    @pytest.mark.parametrize(
        "name,expected",
        [
            # 지수형 ETF — 브랜드 + 지수 키워드 (영문/한글)
            ("삼성 KODEX 미국S&P500 증권상장지수투자신탁[주식]", "index_etf"),
            ("KODEX 코스피", "index_etf"),
            ("TIGER 미국나스닥100", "index_etf"),
            ("ACE 미국S&P500", "index_etf"),
            ("KBSTAR KOSDAQ150선물인버스", "index_etf"),
            ("KODEX MSCI한국TR", "index_etf"),
            # 모호 케이스 — 숫자만 있어서 지수 키워드 매칭 안 됨 → sector로 분류.
            # weekly cycle의 TICKER_TO_INDEX 매핑으로 보정되므로 보수적 fallback OK.
            ("KODEX 200", "sector_etf"),
            # 섹터형 ETF — 브랜드만, 지수 키워드 없음
            ("TIGER 2차전지테마", "sector_etf"),
            ("KODEX 자동차", "sector_etf"),
            ("ARIRANG 글로벌MZ세대소비액티브", "sector_etf"),
            ("RISE AI&로봇", "sector_etf"),
            # 단일주 — 브랜드 없음
            ("삼성전자보통주", "single_stock"),
            ("코리안리재보험보통주", "single_stock"),
            ("SK하이닉스보통주", "single_stock"),
            ("LG디스플레이보통주", "single_stock"),
            # 엣지 케이스
            (None, "single_stock"),
            ("", "single_stock"),
            ("   ", "single_stock"),
            # 대소문자 무관
            ("kodex 미국s&p500", "index_etf"),
            ("Tiger 2차전지", "sector_etf"),
            # 단일주에 우연히 비슷한 문자열이 있어도 false-positive 없게 — ETN은 브랜드라 매칭
            ("ETN_가짜종목", "sector_etf"),
        ],
    )
    def test_classification(self, name, expected):
        assert infer_instrument_type(name) == expected

    def test_deterministic(self):
        """같은 입력 → 같은 출력 (순수 함수)."""
        name = "삼성 KODEX 미국S&P500"
        first = infer_instrument_type(name)
        for _ in range(5):
            assert infer_instrument_type(name) == first

    def test_returns_literal_string(self):
        """결과는 단일주/지수ETF/섹터ETF 셋 중 하나."""
        assert infer_instrument_type("삼성전자") in {"single_stock", "index_etf", "sector_etf"}
