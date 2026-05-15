"""WeeklyMacroReportCrew Tasks (sequential, 2-task)."""
from core.base_task import BaseTask


class MacroSummaryTask(BaseTask):
    description = (
        "이번 주({week_start} ~ {week_end})의 매크로 5지표를 분석하라.\n\n"
        "1. macro_weekly_query Tool을 1회 호출해 5지표의 시작/종료값 + 변화율을 받는다.\n"
        "2. 한국어 3~5줄 요약 작성. 단순 나열이 아니라 **상호작용**을 해석:\n"
        "   - 달러 강세 + 미 금리 하락 = 위험자산에 우호\n"
        "   - WTI 급등 + 인플레이션 우려 = 매크로 비우호\n"
        "   - 등...\n"
        "3. tone 라벨 부여:\n"
        "   - favorable: 위험자산 우호 (성장주/지수 ETF에 긍정적)\n"
        "   - mixed: 혼조 신호\n"
        "   - unfavorable: 위험자산 비우호\n"
        "4. key_drivers: 이번 주 가장 영향력 큰 1~2개 요인 명시.\n\n"
        "데이터 부족(start/end null) 지표는 요약에 명시하되 tone에는 가중치 약하게.\n\n"
        "출력 JSON:\n"
        "{\n"
        '  "summary": "한국어 3~5줄 매크로 요약",\n'
        '  "tone": "favorable"|"mixed"|"unfavorable",\n'
        '  "key_drivers": ["요인1", "요인2"],\n'
        '  "indicators_snapshot": [...]  // macro_weekly_query 결과 그대로\n'
        "}"
    )
    expected_output = (
        "JSON 형식 매크로 주간 요약. summary는 한국어 3~5줄. "
        "tone과 key_drivers는 ETFEvaluator가 컨텍스트로 받음."
    )


class ETFEvaluationTask(BaseTask):
    description = (
        "이전 단계(MacroSummaryTask) 결과의 tone + key_drivers를 컨텍스트로, "
        "ETF 보유 종목 각각에 대해 우호도를 판정하라.\n\n"
        "절차:\n"
        "1. etf_holdings_query Tool을 호출해 ETF 종목 + tracking_index + holder_chat_ids 조회.\n"
        "2. 빈 결과면 즉시 빈 배열 [] 반환 (ETF 보유 사용자 없음).\n"
        "3. 각 ETF에 대해 verdict 판정:\n"
        "   - favorable: tracking_index의 매크로 우호도 + 매크로 tone 같은 방향.\n"
        "     · 예: tracking_index=sp500이면 매크로 tone=favorable + sp500 indicator delta_pct가 양수 → favorable.\n"
        "   - caution: 혼조 신호 (지수와 매크로 tone 엇갈림, 환차손 등 부분 위험).\n"
        "   - unfavorable: 명확한 반대 트렌드 (지수 하락 + 매크로 비우호).\n"
        "   - **tracking_index가 null이면** 매크로 tone만 적용 + 사유에 '추종 지수 미확인'을 명시. "
        "보수적으로 caution 또는 매크로 tone과 동일 verdict.\n"
        "4. 각 ETF의 한국어 한 줄 사유 — 정량 근거(예: '나스닥 주간 +2.1% + DXY 약세') 포함.\n\n"
        "출력 JSON 배열:\n"
        "[\n"
        '  {"ticker": "379800", "name": "...", "tracking_index": "sp500"|null, '
        '"verdict": "favorable"|"caution"|"unfavorable", "reason": "한국어 한 줄"},\n'
        "  ...\n"
        "]\n\n"
        "**언어 강제: reason은 반드시 한국어. 영어 금지.**"
    )
    expected_output = (
        "JSON 배열. ETF 보유 종목 각각의 verdict + 한국어 사유. "
        "ETF 보유자 없으면 빈 배열 [] 반환."
    )
