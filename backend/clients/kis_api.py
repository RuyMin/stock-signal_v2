"""KIS OpenAPI 클라이언트 — backend용 (종목명 조회만).

POST /holdings 처리 중 ticker → 종목명 즉시 조회용. 운영 정책:
- KIS_APP_KEY/SECRET 환경변수 미설정이면 None 반환 (worker-data-collector fallback에 위임)
- HTTP 오류/rt_cd != "0" 시 None 반환 (등록 자체는 진행, name=NULL 후 worker가 보강)
- 토큰은 모듈 단위 메모리 캐시 (24h, 만료 60초 전 갱신)
- httpx.AsyncClient는 lazy init — FastAPI lifespan에 종속되지 않게

worker-data-collector의 KisApiClient에서 fetch_ticker_name + 토큰 관리 부분만 발췌.
"""
import asyncio
import json
import os
import time
from datetime import date as date_type
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()

API_BASE = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
TR_SEARCH_STOCK_INFO = os.getenv("KIS_TR_SEARCH_STOCK_INFO", "CTPF1002R")
TR_DAILY_ITEMCHARTPRICE = os.getenv("KIS_TR_DAILY_ITEMCHARTPRICE", "FHKST03010100")
API_SEARCH_STOCK_INFO = "/uapi/domestic-stock/v1/quotations/search-stock-info"
API_DAILY_ITEMCHARTPRICE = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
CUSTTYPE_PERSONAL = "P"

# 공유 docker volume — backend/crewai/data-collector가 같이 read/write.
# 컨테이너 재시작/멀티 컨테이너에서 1분 재발급 차단 회피.
TOKEN_CACHE_PATH = os.getenv("KIS_TOKEN_CACHE_PATH", "/var/cache/kis/token.json")


def _load_token_cache() -> Optional[tuple[str, float]]:
    """파일 캐시 → (token, expires_at_epoch). 없거나 깨진 파일은 None."""
    try:
        with open(TOKEN_CACHE_PATH) as f:
            data = json.load(f)
        token = data.get("token")
        expires_at = float(data.get("expires_at", 0))
        if token and expires_at > 0:
            return token, expires_at
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, KeyError):
        pass
    return None


def _save_token_cache(token: str, expires_at: float) -> None:
    """토큰 atomic 저장. 실패해도 메모리 캐시로 동작 가능 → warning만."""
    try:
        os.makedirs(os.path.dirname(TOKEN_CACHE_PATH), exist_ok=True)
        tmp = TOKEN_CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"token": token, "expires_at": expires_at}, f)
        os.replace(tmp, TOKEN_CACHE_PATH)
    except OSError as exc:
        logger.warning("kis_token_cache_save_failed", error=str(exc))


class _KisClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def _get_http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_token(self) -> Optional[str]:
        if not APP_KEY or not APP_SECRET:
            return None
        async with self._lock:
            # 1. 메모리 캐시 유효
            if self._token and self._expires_at - time.time() > 60:
                return self._token
            # 2. 파일 캐시 (다른 컨테이너가 발급해뒀을 수 있음)
            cached = _load_token_cache()
            if cached is not None and cached[1] - time.time() > 60:
                self._token, self._expires_at = cached
                logger.info("kis_token_loaded_from_file_cache")
                return self._token
            # 3. KIS 발급 시도
            try:
                resp = await self._get_http().post(
                    f"{API_BASE}/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": APP_KEY,
                        "appsecret": APP_SECRET,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._token = data["access_token"]
                self._expires_at = time.time() + int(data.get("expires_in", 86400))
                _save_token_cache(self._token, self._expires_at)
                logger.info("kis_token_refreshed")
                return self._token
            except Exception as exc:  # noqa: BLE001
                logger.warning("kis_token_failed", error=str(exc))
                # 4. 발급 실패 시 파일 재조회 — 다른 컨테이너가 막 발급했을 수 있음
                cached = _load_token_cache()
                if cached is not None and cached[1] - time.time() > 60:
                    self._token, self._expires_at = cached
                    logger.info("kis_token_loaded_after_failure")
                    return self._token
                return None

    async def fetch_ticker_name(self, ticker: str) -> Optional[str]:
        token = await self._ensure_token()
        if not token:
            return None
        try:
            resp = await self._get_http().get(
                f"{API_BASE}{API_SEARCH_STOCK_INFO}",
                params={"PRDT_TYPE_CD": "300", "PDNO": ticker},
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": APP_KEY,
                    "appsecret": APP_SECRET,
                    "tr_id": TR_SEARCH_STOCK_INFO,
                    "tr_cont": "",
                    "custtype": CUSTTYPE_PERSONAL,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "kis_fetch_ticker_name_http_error", ticker=ticker, error=str(exc)
            )
            return None

        body = resp.json()
        if body.get("rt_cd") != "0":
            logger.warning(
                "kis_fetch_ticker_name_error",
                ticker=ticker, rt_cd=body.get("rt_cd"), msg=body.get("msg1"),
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


    async def fetch_daily_prices(
        self,
        ticker: str,
        date_from: date_type,
        date_to: date_type,
    ) -> dict[date_type, float]:
        """[date_from, date_to] 기간의 일별 종가 dict 반환. 실패/빈 응답 시 빈 dict.

        TR_ID FHKST03010100 (국내주식 기간별 시세). 한 번 호출로 N일치 반환.
        외+기관 평단가 추정에 사용.
        """
        token = await self._ensure_token()
        if not token:
            return {}
        try:
            resp = await self._get_http().get(
                f"{API_BASE}{API_DAILY_ITEMCHARTPRICE}",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": date_from.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": date_to.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": APP_KEY,
                    "appsecret": APP_SECRET,
                    "tr_id": TR_DAILY_ITEMCHARTPRICE,
                    "tr_cont": "",
                    "custtype": CUSTTYPE_PERSONAL,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "kis_daily_prices_http_error", ticker=ticker, error=str(exc)
            )
            return {}

        body = resp.json()
        if body.get("rt_cd") != "0":
            logger.warning(
                "kis_daily_prices_error",
                ticker=ticker, rt_cd=body.get("rt_cd"), msg=body.get("msg1"),
            )
            return {}

        result: dict[date_type, float] = {}
        for row in body.get("output2") or []:
            date_str = (row.get("stck_bsop_date") or "").strip()
            close_str = (row.get("stck_clpr") or "").strip()
            if len(date_str) != 8 or not close_str:
                continue
            try:
                d = date_type(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
                result[d] = float(close_str)
            except (TypeError, ValueError):
                continue
        return result


_singleton = _KisClient()


async def fetch_ticker_name(ticker: str) -> Optional[str]:
    """종목코드 → 종목명. 키 미설정/네트워크 오류 시 None — 호출자는 등록 진행."""
    return await _singleton.fetch_ticker_name(ticker)


async def fetch_daily_prices(
    ticker: str, date_from: date_type, date_to: date_type
) -> dict[date_type, float]:
    """기간 일별 종가 dict. 실패 시 빈 dict (평단가 추정 생략)."""
    return await _singleton.fetch_daily_prices(ticker, date_from, date_to)


async def close() -> None:
    """FastAPI shutdown hook용."""
    await _singleton.aclose()
