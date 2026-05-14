"""StockRecommendationCrew Tasks.

Sequential 실행. Synthesizer가 앞 3개 Task의 결과를 context로 받음.
Synthesizer의 expected_output은 JSON 배열 — main.py가 파싱해서 PG 저장.
"""
from core.base_task import BaseTask


class SignalAnalysisTask(BaseTask):
    description = (
        "거래일 {target_date}의 한국 시장 수급 + 모멘텀 데이터를 분석하라.\n\n"
        "절차 (반드시 순서대로 모든 Tool 호출 수행):\n\n"
        "**1단계 — 보유 종목 식별 (먼저 수행)**:\n"
        "  1.1. holdings_query() 호출. 결과 ticker 목록을 H로 정의 (Holdings Set).\n\n"
        "**2단계 — 신규 매수 후보 도출 (세 경로 OR 결합, 보유 종목 제외)**:\n"
        "  2.1. signal_query(target_date={target_date}, min_consecutive=3): "
        "**경로 A — 연속 매수**. 결과에서 H에 속하지 않은 ticker를 C₁로.\n"
        "  2.2. momentum_query(target_date={target_date}): "
        "그날 모든 종목의 모멘텀 지표. 결과에서 H에 속하지 않은 ticker 중\n"
        "    - **경로 B — 급등 모멘텀**: one_day_net_buy ≥ 10,000,000,000 (100억원)\n"
        "    - **경로 C — 거래량 급증**: volume_ratio ≥ 3.0 AND one_day_net_buy > 0\n"
        "    두 조건 만족하는 ticker를 C₂로.\n"
        "  2.3. N = C₁ ∪ C₂ (중복 제거). **이게 신규 후보 풀**.\n"
        "  2.4. **|N| < 5이면 보강**: signal_query(target_date={target_date}, min_consecutive=1) 호출. "
        "결과 중 H ∪ N에 속하지 않은 ticker를 (agency_net_buy + foreign_net_buy) 합 내림차순으로 정렬해 "
        "상위 (5 - |N|)개를 N에 추가. 운영 초기 sparse 시기에도 신규 추천이 작동하도록 누락 금지.\n\n"
        "**3단계 — 보유 종목 수급/모멘텀 강도 평가**:\n"
        "  3.1. signal_query(target_date={target_date}, min_consecutive=0, tickers=H).\n"
        "  3.2. momentum_query는 2.2에서 이미 받았으니 같은 결과에서 H에 해당하는 row를 사용 (Tool 재호출 불필요).\n\n"
        "**최종 출력 구성**:\n"
        "- tickers = N ∪ H (신규 후보 + 보유 종목, 중복 없음)\n"
        "- new_candidates = N (보유 종목 제외)\n"
        "- analysis: 각 ticker별 수급+모멘텀 한 줄 요약 (정량 데이터 포함)\n\n"
        "**수급/모멘텀 평가 정량 기준**:\n"
        "- agency_net_buy + foreign_net_buy ≥ +1,000,000주 AND consecutive ≥ 2 → '강한 매수 흡수형'.\n"
        "- one_day_net_buy ≥ 100억 → '급등 모멘텀'.\n"
        "- one_day_net_buy ≥ 2 × three_day_avg_net_buy AND three_day_avg_net_buy > 0 → '가속 패턴'.\n"
        "- volume_ratio ≥ 3.0 → '거래량 급증'.\n"
        "- net_buy 합 양수 + consecutive=1 → '단발성 매수'.\n"
        "- net_buy 합 음수 → '매도 우세'.\n"
        "- 데이터 row 없음 → '데이터 없음' ('약세' 단정 금지).\n\n"
        "**단위 주의**:\n"
        "- one_day_net_buy, three_day_avg_net_buy, trading_value의 단위는 **원(KRW)** — 100억=10,000,000,000.\n"
        "- agency_net_buy, foreign_net_buy의 단위는 **주식 수** (signal_query 결과).\n\n"
        "**금지 사항**:\n"
        "- new_candidates에 보유 종목(H의 원소)을 절대 넣지 말 것.\n"
        "- consecutive만 보고 강세/약세 단정 금지 — net_buy 부호/규모 + 모멘텀 컨텍스트 동시 고려.\n\n"
        "출력 JSON: {tickers: [...], new_candidates: [...], analysis: {ticker: '한 줄 요약', ...}}"
    )
    expected_output = (
        "JSON: tickers(신규+보유 합집합) + new_candidates(보유 제외 신규만, 연속/급등/거래량 급증 OR 결합) + "
        "ticker별 수급+모멘텀 요약(**한국어**). new_candidates가 보유 종목과 겹치면 즉시 재정렬."
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
        "summary는 **한국어 한 줄**. "
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
        "JSON: 매크로 5지표 값 + verdict + **한국어 한 줄** 코멘트. "
        "데이터 없으면 verdict='unknown'으로 명시 (단정적 'unfavorable' 금지)."
    )


class SynthesisTask(BaseTask):
    description = (
        "수급·모멘텀·뉴스·매크로·기술적 지표 분석 결과(이전 Task의 context)를 종합하여 "
        "추천 후보 목록을 작성하라.\n\n"
        "**중요: 너의 역할은 정성적 평가(reason 작성 + sentiment/매크로 라벨링)이며, "
        "점수 산정과 분류는 코드가 결정론적으로 처리한다.** 따라서 score를 직접 매기지 말고, "
        "각 ticker에 대해 정확한 sentiment / macro_verdict 라벨과 한국어 reason 3종을 작성하면 된다.\n\n"
        "**대상 종목**: SignalAnalysisTask 출력의 tickers 전체 (= 신규 후보 N + 보유 종목 H). "
        "신규 후보 중 정성적으로 명백히 부적절한 종목(예: 큰 악재 + 매도 우세)만 제외해도 좋다. "
        "그 외에는 **모두 reason과 라벨을 작성해 출력**한다 — 점수 기반 필터링은 코드가 한다.\n\n"
        "거래일: {target_date}, 다음 거래일: {target_trading_date}.\n"
        "{holiday_gap_text}\n\n"
        "**휴장일 갭 처리 가이드** (reason 작성 시 반영):\n"
        "- 갭이 있을 때 직전 거래일의 부정 뉴스가 그날 외국인+기관 강한 순매수와 함께 있으면 "
        "**부정 뉴스 흡수**로 해석하고 reason_news에 그렇게 명시. sentiment는 'neutral' 또는 'positive'.\n"
        "- 갭 동안 발생한 글로벌 호재(미국 반도체 랠리, S&P 사상 최고치 등)는 다음 거래일에 "
        "직접 반영되므로 sentiment 가중.\n\n"
        "**라벨 가이드**:\n"
        "- sentiment: 'strongly_positive' | 'positive' | 'neutral' | 'negative' | 'strongly_negative' | 'none' "
        "(뉴스 자체가 없는 경우만 'none', 있지만 인상이 없으면 'neutral')\n"
        "- macro_verdict: 'favorable' | 'neutral' | 'unfavorable' | 'unknown' "
        "(MacroAnalysisTask 결과 그대로 — 모든 ticker에 동일 값)\n\n"
        "**reason 작성 가이드**:\n"
        "- reason_supply: 수급(외인/기관 순매수, 연속매수일)과 모멘텀(급등/가속/거래량) "
        "관찰을 통합한 한국어 한 줄. 정량 데이터 포함. signals row 없으면 '데이터 미수집(상위 30위 외)' 명시.\n"
        "- reason_news: 뉴스 핵심 한국어 한 줄, 없으면 null.\n"
        "- reason_macro: 매크로 환경 한국어 한 줄.\n\n"
        "**estimated_avg_price**: buy_hedge로 분류될 가능성이 있는 신규 후보에 한해 숫자 추정값 "
        "(추정 안 되면 null). 보유 종목은 항상 null. **분류는 코드가 결정하므로 모든 ticker에 "
        "estimated_avg_price를 함께 출력해두면 코드가 buy_hedge로 분류될 때만 사용한다.**\n\n"
        "최종 출력은 **JSON 배열**만 반환. 다른 텍스트 금지. 각 항목 스키마:\n"
        "{\n"
        '  "ticker": "005930",\n'
        '  "name": null,\n'
        '  "sentiment": "strongly_positive"|"positive"|"neutral"|"negative"|"strongly_negative"|"none",\n'
        '  "macro_verdict": "favorable"|"neutral"|"unfavorable"|"unknown",\n'
        '  "reason_supply": "한국어 한 줄",\n'
        '  "reason_news": "한국어 한 줄 또는 null",\n'
        '  "reason_macro": "한국어 한 줄",\n'
        '  "estimated_avg_price": 숫자|null\n'
        "}\n\n"
        "**언어 강제: 모든 reason 필드는 반드시 한국어로 작성. 영어/번역체 금지.** "
        "사용자가 한국어 텔레그램 알림으로 받기 때문에 영어 출력은 사용자 경험을 해친다."
    )
    expected_output = (
        "JSON 배열. 신규 후보 + 보유 종목 거의 전부 출력 (정성적으로 명백히 부적절한 경우만 제외). "
        "각 항목은 sentiment / macro_verdict 라벨 + reason 3종 + estimated_avg_price 포함. "
        "**점수와 recommendation_type은 출력하지 않는다 — 코드가 산정.** "
        "**모든 reason 필드는 한국어.**"
    )
