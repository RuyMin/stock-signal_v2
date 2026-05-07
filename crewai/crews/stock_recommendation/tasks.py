"""StockRecommendationCrew Tasks.

Sequential 실행. Synthesizer가 앞 3개 Task의 결과를 context로 받음.
Synthesizer의 expected_output은 JSON 배열 — main.py가 파싱해서 PG 저장.
"""
from core.base_task import BaseTask


class SignalAnalysisTask(BaseTask):
    description = (
        "거래일 {target_date}의 한국 시장 수급 데이터를 분석하라.\n\n"
        "절차 (반드시 순서대로 모든 Tool 호출 수행):\n\n"
        "**1단계 — 보유 종목 식별 (먼저 수행)**:\n"
        "  1.1. holdings_query() 호출. 결과 ticker 목록을 H로 정의 (Holdings Set).\n\n"
        "**2단계 — 신규 매수 후보 도출 (보유 종목과 분리)**:\n"
        "  2.1. signal_query(target_date={target_date}, min_consecutive=3): PRD 기본 룰 (3일 연속 매수). "
        "결과에서 H에 속하지 않은 ticker만 추려 신규 후보 N으로 정의.\n"
        "  2.2. **|N| < 5이면 보강**: signal_query(target_date={target_date}, min_consecutive=1) 호출. "
        "결과 중 H에 속하지 않은 ticker를 (agency_net_buy + foreign_net_buy) 합 내림차순으로 정렬해 "
        "상위 (5 - |N|)개를 N에 추가.\n"
        "  → N은 **반드시 보유 종목과 겹치지 않는 신규 후보 리스트**. 운영 초기 시그널 sparse 시기에도 "
        "신규 추천이 작동하도록 2.2 보강 단계는 누락 금지.\n\n"
        "**3단계 — 보유 종목 수급 강도 평가**:\n"
        "  3.1. signal_query(target_date={target_date}, min_consecutive=0, tickers=H): "
        "보유 종목 각각의 실제 net_buy/consecutive 데이터 수집.\n\n"
        "**최종 출력 구성**:\n"
        "- tickers = N ∪ H (신규 후보 + 보유 종목, 중복 없음)\n"
        "- new_candidates = N (보유 종목 제외, 시장 추천 대상 — 매수 헬지/관망 후보)\n"
        "- analysis: 각 ticker별 수급 한 줄 요약 (정량 데이터 포함)\n\n"
        "**수급 평가 정량 기준** (3.1 결과 또는 2단계 결과로 판단):\n"
        "- agency_net_buy + foreign_net_buy ≥ +1,000,000주 AND consecutive ≥ 2 → '강한 매수 흡수형'.\n"
        "- net_buy 합 양수 + consecutive=1 → '단발성 매수' (합이 매우 크면 신규 후보로 의미 있음).\n"
        "- net_buy 합 음수 → '매도 우세'.\n"
        "- 데이터 row 없음 → '데이터 없음' ('약세' 단정 금지).\n\n"
        "**금지 사항**:\n"
        "- new_candidates에 보유 종목(H의 원소)을 절대 넣지 말 것.\n"
        "- consecutive만 보고 강세/약세 단정 금지 — 실제 net_buy 부호/규모 우선.\n\n"
        "출력 JSON: {tickers: [...], new_candidates: [...], analysis: {ticker: '한 줄 요약', ...}}"
    )
    expected_output = (
        "JSON: tickers(신규+보유 합집합) + new_candidates(보유 제외 신규만) + "
        "ticker별 수급 요약. new_candidates가 보유 종목과 겹치면 즉시 재정렬."
    )


class NewsAnalysisTask(BaseTask):
    description = (
        "이전 단계(SignalAnalysisTask)에서 도출된 tickers를 그대로 분석 대상으로 사용하라. "
        "tickers는 이미 시그널 후보 + 보유 종목 합집합으로 구성됨 — 이 단계에서 별도로 holdings를 추가할 필요 없음.\n\n"
        "news_query Tool에 date_from={target_date}, date_to={target_trading_date}, "
        "tickers=이전 단계 결과를 넘겨 직전 거래일~다음 거래일 사이의 모든 뉴스를 조회하라. "
        "**tickers가 빈 배열이면 SignalAnalyzer가 holdings_query를 누락한 것** — 그 경우엔 분석 결과 {} 반환.\n\n"
        "{holiday_gap_text}\n\n"
        "각 뉴스에는 date 필드가 있다 — 반드시 시점을 구분해 분석한다:\n"
        "- date={target_date} 뉴스: 직전 거래일 시점에 알려진 정보. **그 날의 종가/수급에 이미 반영됨**.\n"
        "- date가 그 사이 (휴장일에 발생): 다음 거래일에 처음 시장가에 반영될 신선한 정보.\n"
        "- date={target_trading_date} 뉴스: 당일 시장 시작 전 새벽 글로벌 뉴스 — 가장 큰 즉시 영향.\n\n"
        "감성 평가 시 주의:\n"
        "1. 직전 거래일에 부정 뉴스가 있었으나 그 날 외국인+기관이 강한 순매수였다면 "
        "**시장이 부정 뉴스를 흡수**한 강세 시그널로 해석한다 (단순히 부정으로 처리 금지).\n"
        "2. 휴장일 중 또는 다음 거래일 새벽 글로벌 뉴스(미국 시장 동향, 반도체 섹터 등)는 "
        "직전 거래일 종가에 미반영된 정보이므로 가중치를 더 둔다.\n"
        "3. 분석 회사 목표주가 변경(상향/하향)은 1개 의견일 뿐 — 동일 시점에 반대 톤 뉴스가 있으면 균형 평가.\n\n"
        "뉴스가 없는 종목은 sentiment=null, summary='뉴스 없음'.\n"
        "출력 JSON: {ticker: {sentiment, summary, key_dates: [\"YYYY-MM-DD\", ...]}, ...}"
    )
    expected_output = (
        "JSON: 종목별 {sentiment, summary, key_dates}. "
        "key_dates는 평가에 결정적이었던 뉴스의 발생일. 모든 후보 종목이 키로 포함."
    )


class MacroAnalysisTask(BaseTask):
    description = (
        "다음 한국 거래일 {target_trading_date} 시점에서 가장 신선한 매크로 5지표를 평가하라.\n\n"
        "**중요**: 매크로(미국 국채/달러/WTI/S&P/금)는 글로벌 시장 데이터로 미국 거래일 종가 기준이며, "
        "한국 휴장일(주말, 공휴일)과 무관하게 미국이 거래한 날 모두 데이터가 존재한다. "
        "따라서 기준일은 직전 한국 거래일이 아닌 **다음 한국 거래일({target_trading_date})**이어야 한다 — "
        "한국 시장 시작 직전 가장 최근 미국 종가가 시장에 영향을 미치기 때문.\n\n"
        "절차:\n"
        "1. macro_query Tool을 **near_date={target_trading_date}로 1회만 호출**. "
        "Tool은 그 날짜 이하의 가장 최근 row를 반환한다 — 추가 시도 불필요.\n"
        "2. available=true면 5지표를 한국 주식시장 영향(달러 강세/약세, 미 금리 변화, 원자재 등) 관점에서 "
        "favorable / neutral / unfavorable로 판정.\n"
        "3. available=false면 즉시 verdict='unknown', summary='매크로 데이터 없음'으로 결론. "
        "**다른 날짜로 재시도 금지** (LLM 토큰 낭비).\n\n"
        "출력 JSON: {date, us10y, dxy, wti, sp500, gold, "
        "verdict: 'favorable'|'neutral'|'unfavorable'|'unknown', summary: '한 줄 코멘트'}"
    )
    expected_output = (
        "JSON: 매크로 5지표 값 + verdict + 한 줄 코멘트. "
        "데이터 없으면 verdict='unknown'으로 명시 (단정적 'unfavorable' 금지)."
    )


class SynthesisTask(BaseTask):
    description = (
        "수급·뉴스·매크로 분석 결과(이전 Task의 context)를 종합하여 추천 종목을 결정하라.\n\n"
        "**입력 분리**: SignalAnalysisTask 출력의 `new_candidates`(신규 후보 N) 와 그 외 tickers - N(보유 H)을 "
        "구분해 처리한다.\n\n"
        "**점수 산출** (각 종목 0~100):\n"
        "  - 수급 50%: net_buy 부호/규모, consecutive 가중. 양수 + 강도 큰 → 가점.\n"
        "  - 뉴스 25%: 긍정 +가점 / 부정 -감점 / 없음 중립.\n"
        "  - 매크로 25%: favorable +가점 / unfavorable -감점 / unknown 중립.\n\n"
        "**분류 룰** (이건 코드가 후처리로 강제 재분류하지만 prompt에서도 동일 룰을 따라 점수를 합리적으로 매겨라):\n"
        "  - score ≥ 70 → buy_hedge\n"
        "  - 50 ≤ score < 70 → watch\n"
        "  - score < 50 AND 보유 종목(H) → exit_alert\n"
        "  - score < 50 AND 신규 후보(N) → 출력하지 말 것 (시장 추천 가치 없음)\n\n"
        "**출력 한도** (보유와 신규 분리):\n"
        "  - 보유 종목 H: **5개 모두 출력** (점수에 따라 자동 분류). 한도 없음.\n"
        "  - 신규 후보 N: **score 상위 3개까지만 출력** (score≥50인 종목 중). score<50은 제외.\n\n"
        "**탈출 시그널 가이드** (보유 종목 한정):\n"
        "수급 매도 우세 + 뉴스 부정 + 매크로 비우호 중 2개 이상 강하게 발생한 보유 종목은 **score를 50 미만으로 매겨** "
        "exit_alert로 분류되도록 한다 (코드가 score 기반 강제 분류).\n\n"
        "거래일: {target_date}, 다음 거래일: {target_trading_date}.\n"
        "{holiday_gap_text}\n\n"
        "휴장일 갭 처리 가이드:\n"
        "- 갭이 있을 때 직전 거래일의 부정 뉴스가 그날 외국인+기관 강한 순매수와 함께 있으면 "
        "**부정 뉴스 흡수**로 해석하고 수급 점수를 깎지 말 것.\n"
        "- 갭 동안 발생한 글로벌 호재(미국 반도체 랠리, S&P 사상 최고치 등)는 다음 거래일 "
        "한국 시장에 직접 반영되므로 뉴스 점수에 가중.\n\n"
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
