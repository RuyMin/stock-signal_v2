"""StockRecommendationCrew Tasks.

Sequential 실행. Synthesizer가 앞 3개 Task의 결과를 context로 받음.
Synthesizer의 expected_output은 JSON 배열 — main.py가 파싱해서 PG 저장.
"""
from core.base_task import BaseTask


class SignalAnalysisTask(BaseTask):
    description = (
        "거래일 {target_date}에 기관/외국인이 3일 이상 연속 순매수한 종목 풀을 도출하라.\n"
        "signal_query Tool을 사용하여 데이터를 조회하고, 각 종목의 수급 흐름을 한 줄로 요약하라.\n"
        "출력은 JSON 객체: {tickers: [...], analysis: {ticker: '수급 한 줄 요약', ...}}"
    )
    expected_output = (
        "JSON: 후보 종목 리스트 + 종목별 수급 요약 한 줄. 종목 0개여도 빈 배열 반환."
    )


class NewsAnalysisTask(BaseTask):
    description = (
        "이전 단계에서 도출된 후보 종목 + 보유 종목의 거래일 {target_date} 뉴스를 분석하라.\n"
        "news_query Tool에 대상 ticker 목록을 넘겨 뉴스를 조회하고, 종목별로 "
        "감성(positive/negative/neutral) + 헤드라인 한 줄 요약을 생성하라.\n"
        "뉴스가 없는 종목은 sentiment=null, summary='뉴스 없음'으로 표기.\n"
        "출력 JSON: {ticker: {sentiment, summary}, ...}"
    )
    expected_output = "JSON: 종목별 {sentiment, summary}. 모든 후보 종목이 키로 포함."


class MacroAnalysisTask(BaseTask):
    description = (
        "거래일 {target_date} 시점의 가장 최근 매크로 5지표를 macro_query Tool로 조회하고, "
        "한국 주식시장에 미치는 영향을 판정하라.\n"
        "출력 JSON: {date, us10y, dxy, wti, sp500, gold, verdict: 'favorable'|'neutral'|'unfavorable', summary: '한 줄 코멘트'}"
    )
    expected_output = "JSON: 매크로 5지표 값 + verdict + 한 줄 코멘트."


class SynthesisTask(BaseTask):
    description = (
        "수급·뉴스·매크로 분석 결과(이전 Task의 context)를 종합하여 추천 종목을 결정하라.\n\n"
        "절차:\n"
        "1. holdings_query Tool로 사용자 보유 종목 목록을 조회.\n"
        "2. 각 후보 종목에 대해 가이드 가중치로 점수 산출 (0-100):\n"
        "   - 수급 50% (연속 매수일/순매수액 강도)\n"
        "   - 뉴스 25% (긍정 +가점 / 부정 -감점 / 없음 중립)\n"
        "   - 매크로 25% (우호 +가점 / 비우호 -감점)\n"
        "3. 단계 분류:\n"
        "   - 매수 헬지: score ≥ 70 (모든 종목 대상)\n"
        "   - 관망: 50 ≤ score < 70 (모든 종목 대상)\n"
        "   - 탈출 경보: score < 50 AND 보유 종목 (보유 종목 한정)\n"
        "4. 탈출 시그널(매수→매도 전환 / 매도 우세 / 부정 뉴스 / 매크로 비우호) 중 "
        "2개 이상 동시 발생한 보유 종목은 점수와 무관하게 탈출 경보로 분류.\n"
        "5. 매수 헬지/관망은 합산 5종목 이내(스코어 상위). 탈출 경보는 모두 포함.\n\n"
        "거래일: {target_date}, 다음 거래일: {target_trading_date}.\n\n"
        "최종 출력은 **JSON 배열**만 반환. 다른 텍스트 금지. 각 항목 스키마:\n"
        "{\n"
        '  "ticker": "005930",\n'
        '  "name": null,\n'
        '  "recommendation_type": "buy_hedge"|"watch"|"exit_alert",\n'
        '  "score": 0-100 정수,\n'
        '  "reason_supply": "한 줄",\n'
        '  "reason_news": "한 줄 또는 null",\n'
        '  "reason_macro": "한 줄",\n'
        '  "estimated_avg_price": 숫자|null  (buy_hedge일 때만)\n'
        "}"
    )
    expected_output = (
        "JSON 배열. 5종목 이내(탈출 경보 제외) + 보유 종목 탈출 경보 전부. "
        "조건 충족 종목 0개면 빈 배열 [] 반환."
    )
