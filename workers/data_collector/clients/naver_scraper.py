"""네이버 금융 종목별 뉴스 스크래핑.

규칙 (SPEC §3 뉴스 스크래핑 방침):
- User-Agent 변조
- 1~3초 랜덤 딜레이
- HTTP 429/403 감지 시 해당 종목 스킵 (전체 작업은 계속)

페이지 구조 (2026-04-30 운영 검증):
- URL: /item/news_news.nhn?code={ticker}
- 인코딩: EUC-KR(cp949) — Content-Type charset 헤더 누락 가능성 있어 명시적 디코딩
- 구조: <table class="type5"> 안의 <tr> 행, 각 행은
        <td class="title"><a class="tit" href="...">제목</a></td>
        <td class="info">언론사</td>
        <td class="date"> YYYY.MM.DD HH:MM</td>
- href는 /item/news_read.naver?article_id=...&code=... (상대경로)
"""
import asyncio
import random
from dataclasses import dataclass
from datetime import date
from typing import Optional

import httpx
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


@dataclass(slots=True)
class NewsRow:
    date: date
    ticker: str
    title: str
    url: Optional[str]


class NaverNewsScraper:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_for_ticker(self, ticker: str, target_date: date) -> list[NewsRow]:
        """단일 종목의 당일 뉴스 헤드라인 목록.

        차단 감지 시 빈 리스트 반환 + 경고 로그.
        """
        url = f"https://finance.naver.com/item/news_news.nhn?code={ticker}"
        headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://finance.naver.com/"}

        # 랜덤 딜레이
        await asyncio.sleep(random.uniform(1.0, 3.0))

        client = await self._get_client()
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("naver_scrape_failed", ticker=ticker, error=str(exc))
            return []

        if resp.status_code in (403, 429):
            logger.warning("naver_scrape_blocked", ticker=ticker, status=resp.status_code)
            return []
        if resp.status_code >= 400:
            logger.warning("naver_scrape_failed", ticker=ticker, status=resp.status_code)
            return []

        # 명시적 EUC-KR 디코딩 — httpx 자동 추정에 맡기지 않음
        html = resp.content.decode("cp949", errors="replace")
        return self._parse(html, ticker, target_date)

    @staticmethod
    def _parse(html: str, ticker: str, target_date: date) -> list[NewsRow]:
        """`<table class="type5">` 행에서 제목+url 추출. 종목당 최대 10건."""
        soup = BeautifulSoup(html, "lxml")
        rows: list[NewsRow] = []
        # td.title 안의 a.tit가 정확한 셀렉터 (운영 검증 2026-04-30)
        for a in soup.select("table.type5 td.title a.tit"):
            title = a.get_text(strip=True)
            if not title:
                continue
            href = a.get("href")
            full_url = (
                f"https://finance.naver.com{href}" if href and href.startswith("/") else href
            )
            rows.append(NewsRow(date=target_date, ticker=ticker, title=title, url=full_url))
        return rows[:10]
