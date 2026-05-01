"""네이버 금융 종목 뉴스 스크래퍼 단위 테스트.

실제 페이지 구조를 reduce한 fixture로 _parse 검증 + httpx mock으로 fetch 흐름 검증.
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


# 실제 네이버 페이지 구조 축소판 (2026-04-30 캡처 기반).
# EUC-KR 환경에서 동작하는지도 함께 검증.
SAMPLE_HTML = """\
<html><head><meta charset="euc-kr"></head><body>
<table summary="종목뉴스의 제목, 정보제공, 날짜" cellspacing="0" class="type5">
  <caption>종목뉴스</caption>
  <tr>
    <th scope="col">제목</th>
    <th scope="col">정보제공</th>
    <th scope="col">날짜</th>
  </tr>
  <tr class="first">
    <td class="title">
      <a href="/item/news_read.naver?article_id=0000142525&office_id=658&code=005930"
         class="tit" target="_top">삼성전자 반도체 신공정 54조 투자...경쟁력 강화</a>
    </td>
    <td class="info">조선비즈</td>
    <td class="date"> 2026.04.30 12:45</td>
  </tr>
  <tr>
    <td class="title">
      <a href="/item/news_read.naver?article_id=0008921180&office_id=421&code=005930"
         class="tit" target="_top">[IR]삼성전자, 2Q '7나노' HBM4E 공급</a>
    </td>
    <td class="info">뉴스1</td>
    <td class="date"> 2026.04.30 12:42</td>
  </tr>
  <tr>
    <td class="title">
      <a href="/item/news_read.naver?article_id=0008921175&office_id=421&code=005930"
         class="tit" target="_top">  </a>
    </td>
    <td class="info">뉴스1</td>
    <td class="date"> 2026.04.30 12:39</td>
  </tr>
</table>
<!-- 다른 페이지 영역의 a 태그 - 매칭되면 안 됨 -->
<a class="tit" href="/some/other.html">관련 검색어</a>
</body></html>
"""


def _import_scraper():
    from clients.naver_scraper import NaverNewsScraper, NewsRow
    return NaverNewsScraper, NewsRow


class TestParse:
    def test_parse_extracts_titles_and_urls(self):
        NaverNewsScraper, _ = _import_scraper()
        rows = NaverNewsScraper._parse(SAMPLE_HTML, "005930", date(2026, 4, 30))
        # 빈 제목 1건 제외 → 2건만
        assert len(rows) == 2
        assert rows[0].title.startswith("삼성전자 반도체")
        assert rows[0].url.startswith("https://finance.naver.com/item/news_read.naver?")
        assert rows[0].ticker == "005930"
        assert rows[0].date == date(2026, 4, 30)

    def test_parse_ignores_a_tit_outside_type5_table(self):
        """table.type5 외부의 a.tit는 매칭되지 않아야 — 셀렉터 정확성."""
        NaverNewsScraper, _ = _import_scraper()
        rows = NaverNewsScraper._parse(SAMPLE_HTML, "005930", date(2026, 4, 30))
        # SAMPLE에는 외부 a.tit 1건("관련 검색어")이 있는데 결과에 포함 안 됨
        assert all(r.title != "관련 검색어" for r in rows)

    def test_parse_caps_at_10(self):
        NaverNewsScraper, _ = _import_scraper()
        # 12개 row 생성
        many_rows = "".join(
            f'<tr><td class="title"><a href="/item/news_read.naver?article_id={i}" class="tit">기사 {i}</a></td>'
            f'<td class="info">언론{i}</td><td class="date"> 2026.04.30 10:00</td></tr>'
            for i in range(12)
        )
        html = f'<table class="type5"><tr><th>x</th></tr>{many_rows}</table>'
        rows = NaverNewsScraper._parse(html, "005930", date(2026, 4, 30))
        assert len(rows) == 10

    def test_parse_empty_when_no_table(self):
        NaverNewsScraper, _ = _import_scraper()
        rows = NaverNewsScraper._parse("<html></html>", "005930", date(2026, 4, 30))
        assert rows == []


class TestFetchForTicker:
    @pytest.mark.asyncio
    async def test_decodes_euckr_response_without_charset_header(self, monkeypatch):
        """Content-Type charset 헤더 없는 EUC-KR 응답도 cp949로 정상 디코딩."""
        # asyncio.sleep을 즉시 끝나게
        async def _no_sleep(*_a, **_kw):
            return None
        monkeypatch.setattr("clients.naver_scraper.asyncio.sleep", _no_sleep)

        NaverNewsScraper, _ = _import_scraper()
        encoded = SAMPLE_HTML.encode("cp949")

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://finance.naver.com/item/news_news.nhn").mock(
                return_value=Response(
                    200,
                    content=encoded,
                    # 일부러 charset 헤더 누락
                    headers={"Content-Type": "text/html"},
                )
            )
            scraper = NaverNewsScraper()
            try:
                rows = await scraper.fetch_for_ticker("005930", date(2026, 4, 30))
            finally:
                await scraper.aclose()

        assert len(rows) == 2
        assert "삼성전자" in rows[0].title

    @pytest.mark.asyncio
    async def test_returns_empty_on_429_blocked(self, monkeypatch):
        async def _no_sleep(*_a, **_kw):
            return None
        monkeypatch.setattr("clients.naver_scraper.asyncio.sleep", _no_sleep)

        NaverNewsScraper, _ = _import_scraper()
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://finance.naver.com/item/news_news.nhn").mock(
                return_value=Response(429, content=b"")
            )
            scraper = NaverNewsScraper()
            try:
                rows = await scraper.fetch_for_ticker("005930", date(2026, 4, 30))
            finally:
                await scraper.aclose()
        assert rows == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_403_blocked(self, monkeypatch):
        async def _no_sleep(*_a, **_kw):
            return None
        monkeypatch.setattr("clients.naver_scraper.asyncio.sleep", _no_sleep)

        NaverNewsScraper, _ = _import_scraper()
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://finance.naver.com/item/news_news.nhn").mock(
                return_value=Response(403, content=b"")
            )
            scraper = NaverNewsScraper()
            try:
                rows = await scraper.fetch_for_ticker("005930", date(2026, 4, 30))
            finally:
                await scraper.aclose()
        assert rows == []
