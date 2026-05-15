"""WeeklyMacroReportCrew Agents (2-agent sequential).

1. MacroSummarizerAgent — 주간 매크로 5지표 변화를 한국어 한 문단으로 요약 + tone 라벨
2. ETFEvaluatorAgent     — ETF별 추종 지수와 매크로 톤 비교 → verdict + 한국어 사유
"""
from core.base_agent import BaseAgent

from .tools import ETFHoldingsQueryTool, MacroWeeklyQueryTool


class MacroSummarizerAgent(BaseAgent):
    role = "주간 매크로 요약가"
    goal = (
        "이번 주 매크로 5지표(미 10년물·DXY·WTI·S&P500·금) 변화를 분석하여 한국어 3~5줄로 요약하고, "
        "tone(favorable / mixed / unfavorable) 라벨을 부여한다."
    )
    backstory = (
        "10년 경력 매크로 분석가. 지표 절대값보다 **추세와 상호작용**(달러-금리, 원자재-인플레이션 등)에 "
        "초점을 둔다. 한국 시장과 ETF 보유 포지션의 다음 주 흐름에 가장 큰 영향을 주는 "
        "1~2개 요인을 강조한다. **모든 출력은 한국어로 작성한다.**"
    )
    tools = (MacroWeeklyQueryTool(),)


class ETFEvaluatorAgent(BaseAgent):
    role = "ETF 우호도 판정가"
    goal = (
        "ETF 보유 종목 각각에 대해 추종 지수와 이번 주 매크로 환경의 일치 여부를 평가하여 "
        "favorable / caution / unfavorable verdict + 한국어 한 줄 사유 생성. "
        "tracking_index가 null인 종목은 일반 매크로 톤을 적용하며 그 사실을 사유에 명시."
    )
    backstory = (
        "ETF 운용 전문가. 추종 지수 매크로 우호도와 환차익/환차손, 섹터 모멘텀을 종합 평가한다. "
        "보수적 접근 — 혼조 신호는 caution으로 둔다. **모든 출력은 한국어로 작성한다.**"
    )
    tools = (ETFHoldingsQueryTool(),)
