"""KIS OpenAPI 클라이언트 — crewai용 (sync, 종목명 조회만).

crew.py on_complete() INSERT 직전에 ticker → 종목명을 채우기 위해 사용.
backend/clients/kis_api.py와 같은 책임이지만 crewai는 동기 환경(psycopg sync pool)이라
`httpx.Client` 사용. 토큰은 모듈 단위 메모리 캐시 (24h, 만료 60초 전 갱신).

운영 정책:
- KIS_APP_KEY/SECRET 미설정이면 None 반환 (worker-data-collector 사이클 폴백에 위임)
- HTTP 오류/rt_cd != "0" 시 None — INSERT는 NULL로 진행, notifier가 holdings.name 폴백 시도
"""
import json
import os
import time
from threading import Lock
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()

API_BASE = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
TR_SEARCH_STOCK_INFO = os.getenv("KIS_TR_SEARCH_STOCK_INFO", "CTPF1002R")
API_SEARCH_STOCK_INFO = "/uapi/domestic-stock/v1/quotations/search-stock-info"
CUSTTYPE_PERSONAL = "P"

# 공유 docker volume — backend/crewai/data-collector가 같이 read/write.
TOKEN_CACHE_PATH = os.getenv("KIS_TOKEN_CACHE_PATH", "/var/cache/kis/token.json")


def _load_token_cache() -> Optional[tuple[str, float]]:
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
        self._lock = Lock()
        self._client: Optional[httpx.Client] = None

    def _get_http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(8.0))
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_token(self) -> Optional[str]:
        if not APP_KEY or not APP_SECRET:
            return None
        with self._lock:
            # 1. 메모리 캐시
            if self._token and self._expires_at - time.time() > 60:
                return self._token
            # 2. 파일 캐시
            cached = _load_token_cache()
            if cached is not None and cached[1] - time.time() > 60:
                self._token, self._expires_at = cached
                logger.info("kis_token_loaded_from_file_cache")
                return self._token
            # 3. KIS 발급
            try:
                resp = self._get_http().post(
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
                # 4. 발급 실패 → 파일 재조회
                cached = _load_token_cache()
                if cached is not None and cached[1] - time.time() > 60:
                    self._token, self._expires_at = cached
                    logger.info("kis_token_loaded_after_failure")
                    return self._token
                return None

    def fetch_ticker_name(self, ticker: str) -> Optional[str]:
        token = self._ensure_token()
        if not token:
            return None
        try:
            resp = self._get_http().get(
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


_singleton = _KisClient()


def fetch_ticker_name(ticker: str) -> Optional[str]:
    """종목코드 → 종목명. 키 미설정/네트워크 오류 시 None."""
    return _singleton.fetch_ticker_name(ticker)


def close() -> None:
    _singleton.close()
