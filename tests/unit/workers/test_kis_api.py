"""KIS API 클라이언트 단위 테스트.

httpx 호출은 respx로 mock. 실제 KIS 호출 없음 — 운영 검증은 별도 smoke test.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest
import respx
from httpx import Response

_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "data_collector")
)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)


BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_URL = f"{BASE_URL}/oauth2/tokenP"
SIGNALS_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
NAME_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/search-stock-info"


def _make_client():
    from clients.kis_api import KisApiClient
    return KisApiClient(app_key="ak", app_secret="as", base_url=BASE_URL)


class TestKisToken:
    @pytest.mark.asyncio
    async def test_token_issued_and_cached(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            tok = mock.post(TOKEN_URL).mock(
                return_value=Response(
                    200, json={"access_token": "tok-1", "expires_in": 86400}
                )
            )
            t1 = await client._ensure_token()
            t2 = await client._ensure_token()
            assert t1 == "tok-1"
            assert t2 == "tok-1"
            assert tok.call_count == 1  # 두 번째는 캐시
        await client.aclose()


class TestFetchSignals:
    @pytest.mark.asyncio
    async def test_parses_output_rows(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok", "expires_in": 86400})
            )
            mock.get(SIGNALS_URL).mock(
                return_value=Response(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "MCA00000",
                        "msg1": "정상",
                        "output": [
                            {
                                "mksc_shrn_iscd": "005930",
                                "hts_kor_isnm": "삼성전자",
                                "frgn_ntby_qty": "1500000",
                                "orgn_ntby_qty": "800000",
                            },
                            {
                                "mksc_shrn_iscd": "000660",
                                "hts_kor_isnm": "SK하이닉스",
                                "frgn_ntby_qty": "500,000",  # 콤마 포함도 허용
                                "orgn_ntby_qty": "300000",
                            },
                        ],
                    },
                )
            )
            rows = await client.fetch_signals(date(2026, 4, 30))

        assert len(rows) == 2
        s1 = next(r for r in rows if r.ticker == "005930")
        assert s1.foreign_net_buy == 1_500_000
        assert s1.agency_net_buy == 800_000
        assert s1.foreign_buy is None  # 가집계는 분리값 없음
        s2 = next(r for r in rows if r.ticker == "000660")
        assert s2.foreign_net_buy == 500_000
        await client.aclose()

    @pytest.mark.asyncio
    async def test_skips_zero_zero_rows(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok", "expires_in": 86400})
            )
            mock.get(SIGNALS_URL).mock(
                return_value=Response(
                    200,
                    json={
                        "rt_cd": "0",
                        "output": [
                            {"mksc_shrn_iscd": "005930", "frgn_ntby_qty": 0, "orgn_ntby_qty": 0},
                            {"mksc_shrn_iscd": "000660", "frgn_ntby_qty": 100, "orgn_ntby_qty": 0},
                        ],
                    },
                )
            )
            rows = await client.fetch_signals(date(2026, 4, 30))
        assert len(rows) == 1
        assert rows[0].ticker == "000660"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_returns_empty_on_rt_cd_error(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok", "expires_in": 86400})
            )
            mock.get(SIGNALS_URL).mock(
                return_value=Response(
                    200,
                    json={"rt_cd": "1", "msg_cd": "ERR", "msg1": "fail", "output": []},
                )
            )
            rows = await client.fetch_signals(date(2026, 4, 30))
        assert rows == []
        await client.aclose()

    @pytest.mark.asyncio
    async def test_request_includes_tr_id_and_token(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok-xyz", "expires_in": 86400})
            )
            route = mock.get(SIGNALS_URL).mock(
                return_value=Response(200, json={"rt_cd": "0", "output": []})
            )
            await client.fetch_signals(date(2026, 4, 30))

            req = route.calls.last.request
            assert req.headers["tr_id"] == "FHPTJ04400000"
            assert req.headers["authorization"] == "Bearer tok-xyz"
            assert req.headers["custtype"] == "P"
            # 쿼리 파라미터 검증
            assert "FID_RANK_SORT_CLS_CODE=0" in str(req.url)
        await client.aclose()


class TestFetchTickerName:
    @pytest.mark.asyncio
    async def test_returns_prdt_name(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok", "expires_in": 86400})
            )
            mock.get(NAME_URL).mock(
                return_value=Response(
                    200,
                    json={"rt_cd": "0", "output": {"prdt_name": "삼성전자"}},
                )
            )
            name = await client.fetch_ticker_name("005930")
        assert name == "삼성전자"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_returns_none_on_rt_cd_error(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok", "expires_in": 86400})
            )
            mock.get(NAME_URL).mock(
                return_value=Response(200, json={"rt_cd": "1", "msg1": "not found"})
            )
            name = await client.fetch_ticker_name("999999")
        assert name is None
        await client.aclose()

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        client = _make_client()
        with respx.mock(assert_all_called=False) as mock:
            mock.post(TOKEN_URL).mock(
                return_value=Response(200, json={"access_token": "tok", "expires_in": 86400})
            )
            mock.get(NAME_URL).mock(return_value=Response(500))
            name = await client.fetch_ticker_name("005930")
        assert name is None
        await client.aclose()
