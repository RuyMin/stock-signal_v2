"""한국투자증권 OpenAPI 클라이언트.

OAuth 토큰 발급 + 외국인·기관 매매 가집계 + 종목명 조회.

운영 검증 완료(2026-04-30) — KIS 공식 GitHub 샘플 기반 endpoint/TR_ID:
- 외국인기관 매매종목가집계: /uapi/domestic-stock/v1/quotations/foreign-institution-total
  TR_ID FHPTJ04400000 — HTS [0440] 화면. 외국인 09:30/11:20/13:20/14:30,
  기관 10:00/11:20/13:20/14:30에 갱신. 시각 ±10분 변동 가능.
- 종목명 조회: /uapi/domestic-stock/v1/quotations/search-stock-info
  TR_ID CTPF1002R — 주식기본조회 [v1_국내주식-067].

가집계 API 특성: 당일 누적 net 수량/금액만 제공 (개별 buy/sell 분리값 없음).
DB의 signals.{agency,foreign}_{buy,sell}는 NULL로 두고 net만 채운다.

Rate limit: 실전 REST 초당 20건. 일별 1회 배치라 여유 있음.
holdings 종목명 채우기는 종목당 0.05초 간격 권장.
"""
import asyncio
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()

# 환경변수로 TR_ID 오버라이드 가능 — 모의/실전 또는 KIS가 변경할 경우 대비
TR_FOREIGN_INSTITUTION_TOTAL = os.getenv("KIS_TR_FOREIGN_INSTITUTION_TOTAL", "FHPTJ04400000")
TR_SEARCH_STOCK_INFO = os.getenv("KIS_TR_SEARCH_STOCK_INFO", "CTPF1002R")

API_FOREIGN_INSTITUTION_TOTAL = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
API_SEARCH_STOCK_INFO = "/uapi/domestic-stock/v1/quotations/search-stock-info"

CUSTTYPE_PERSONAL = "P"  # 개인 — 법인은 'B'


@dataclass(slots=True)
class SignalRow:
    date: date
    ticker: str
    agency_buy: Optional[int]
    agency_sell: Optional[int]
    agency_net_buy: int
    foreign_buy: Optional[int]
    foreign_sell: Optional[int]
    foreign_net_buy: int


@dataclass(slots=True)
class TickerInfo:
    ticker: str
    name: str


class KisApiClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str = "https://openapi.koreainvestment.com:9443",
    ) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = base_url
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_token(self) -> str:
        # 토큰 만료 60초 전이면 갱신
        if self._token and self._token_expires_at - time.time() > 60:
            return self._token

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 86400))
        logger.info("kis_token_refreshed", expires_in=data.get("expires_in"))
        return self._token  # type: ignore[return-value]

    def _build_headers(self, tr_id: str) -> dict[str, str]:
        assert self._token is not None
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "tr_cont": "",
            "custtype": CUSTTYPE_PERSONAL,
        }

    @staticmethod
    def _to_int_or_none(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    async def fetch_signals(self, target_date: date) -> list[SignalRow]:
        """대상 거래일 외국인·기관 순매수 가집계 상위 종목.

        FID_INPUT_ISCD 옵션:
          0000: 전체 / 0001: 코스피 / 1001: 코스닥
          (env KIS_SIGNAL_MARKET_SCOPE로 오버라이드 가능 — 기본 0000)
        FID_RANK_SORT_CLS_CODE: 0=순매수상위, 1=순매도상위

        쿼리는 1회로 상위 30개 내외(API 응답 크기) 반환. SPEC §3 의도 충족 —
        '상위 종목 풀 → consecutive_buy_days 계산'이라 daily 30종 가량이면 충분.

        반환된 데이터에는 buy/sell 분리값이 없어 SignalRow.{agency,foreign}_{buy,sell}는 None.
        agency_net_buy / foreign_net_buy만 채워진다.
        """
        await self._ensure_token()
        market_scope = os.getenv("KIS_SIGNAL_MARKET_SCOPE", "0000")

        url = f"{self.base_url}{API_FOREIGN_INSTITUTION_TOTAL}"
        params = {
            "FID_COND_MRKT_DIV_CODE": "V",
            "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": market_scope,
            "FID_DIV_CLS_CODE": "0",       # 0:수량정렬
            "FID_RANK_SORT_CLS_CODE": "0",  # 0:순매수상위
            "FID_ETC_CLS_CODE": "0",        # 0:전체
        }
        headers = self._build_headers(TR_FOREIGN_INSTITUTION_TOTAL)

        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        if body.get("rt_cd") != "0":
            logger.error(
                "kis_fetch_signals_error",
                rt_cd=body.get("rt_cd"),
                msg_cd=body.get("msg_cd"),
                msg=body.get("msg1"),
            )
            return []

        rows = body.get("output") or []
        result: list[SignalRow] = []
        for row in rows:
            ticker = (row.get("mksc_shrn_iscd") or "").strip()
            if not ticker:
                continue
            agency_net = self._to_int_or_none(row.get("orgn_ntby_qty")) or 0
            foreign_net = self._to_int_or_none(row.get("frgn_ntby_qty")) or 0
            # 둘 다 0이면 의미 없는 row → 스킵
            if agency_net == 0 and foreign_net == 0:
                continue
            result.append(
                SignalRow(
                    date=target_date,
                    ticker=ticker,
                    agency_buy=None,
                    agency_sell=None,
                    agency_net_buy=agency_net,
                    foreign_buy=None,
                    foreign_sell=None,
                    foreign_net_buy=foreign_net,
                )
            )
        logger.info("kis_signals_collected", count=len(result), market_scope=market_scope)
        return result

    async def fetch_ticker_name(self, ticker: str) -> Optional[str]:
        """종목코드 → 종목명. holdings 추가 직후 worker가 채울 때 사용.

        rate limit 회피용 0.05초 sleep 포함 — 다수 종목 호출 시 안전.
        """
        await self._ensure_token()
        await asyncio.sleep(0.05)

        url = f"{self.base_url}{API_SEARCH_STOCK_INFO}"
        params = {"PRDT_TYPE_CD": "300", "PDNO": ticker}
        headers = self._build_headers(TR_SEARCH_STOCK_INFO)

        try:
            resp = await self._client.get(url, params=params, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("kis_fetch_ticker_name_http_error", ticker=ticker, error=str(exc))
            return None

        body = resp.json()
        if body.get("rt_cd") != "0":
            logger.warning(
                "kis_fetch_ticker_name_error",
                ticker=ticker,
                rt_cd=body.get("rt_cd"),
                msg=body.get("msg1"),
            )
            return None

        output = body.get("output")
        if isinstance(output, list):
            output = output[0] if output else None
        if not isinstance(output, dict):
            return None
        name = (output.get("prdt_name") or "").strip() or None
        if name:
            logger.info("kis_ticker_name_resolved", ticker=ticker, name=name)
        return name
