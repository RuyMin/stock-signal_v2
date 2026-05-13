# Requirements Document

## Introduction

본 문서는 stock-signal 프로젝트에 모멘텀 기반 신호 수집 및 평가 기능을 추가하기 위한 요구사항을 정의한다. 현재 시스템은 "기관·외국인 3일 연속 매수" 단일 조건으로 종목을 선정하는데, 이는 후행 지표로서 고점 근처 진입 위험이 있고 급등주나 갑작스런 호재를 추격하지 못하는 한계가 있다. 본 기능은 급등 모멘텀, 가속 패턴, 거래량 급증, 기술적 지표를 추가하여 AI 추천의 정확도와 적시성을 향상시킨다.

## Glossary

- **System**: stock-signal 전체 시스템
- **Data_Collector**: worker-data-collector 서비스 (수급·매크로·뉴스 수집)
- **Signals_Table**: PostgreSQL signals 테이블 (수급 시계열 저장)
- **Signal_Analyzer**: CrewAI SignalAnalysisTask (종목 후보 도출 및 수급 평가)
- **Yfinance_Client**: yfinance 라이브러리를 사용하는 클라이언트 모듈
- **Consecutive_Buy_Days**: 기관 또는 외국인이 연속으로 순매수한 일수
- **Net_Buy**: 순매수량 (매수량 - 매도량)
- **Volume_Ratio**: 당일 거래량 / 20일 평균 거래량
- **RSI**: Relative Strength Index (상대강도지수, 0~100)
- **MA_Alignment**: 이동평균선 배열 상태 (5일/20일/60일 정배열 여부)
- **Bollinger_Position**: 볼린저밴드 내 현재가 위치 (0~1, 0=하단, 1=상단)
- **Trading_Value**: 거래대금 (원화)
- **Momentum_Signal**: 급등 모멘텀 또는 가속 패턴을 감지한 신호
- **KIS_API**: 한국투자증권 OpenAPI (수급 데이터 소스)

## Requirements

### Requirement 1: 모멘텀 지표 저장을 위한 스키마 확장

**User Story:** As a system administrator, I want to extend the signals table schema to store momentum indicators, so that the AI can evaluate stocks using both traditional and momentum-based signals.

#### Acceptance Criteria

1. THE Signals_Table SHALL include a column named one_day_net_buy of type BIGINT to store the sum of agency_net_buy and foreign_net_buy
2. THE Signals_Table SHALL include a column named three_day_avg_net_buy of type BIGINT to store the 3-day moving average of one_day_net_buy
3. THE Signals_Table SHALL include a column named volume_ratio of type NUMERIC(6,2) to store the ratio of current day volume to 20-day average volume
4. THE Signals_Table SHALL include a column named rsi of type NUMERIC(5,2) to store the 14-period RSI value
5. THE Signals_Table SHALL include a column named ma_alignment of type VARCHAR(20) to store moving average alignment status
6. THE Signals_Table SHALL include a column named bollinger_position of type NUMERIC(4,3) to store the position within Bollinger Bands
7. THE Signals_Table SHALL include a column named trading_value of type BIGINT to store the trading value in KRW
8. THE System SHALL create a database migration script to add these columns without data loss
9. THE System SHALL set default values of NULL for all new columns in existing rows

### Requirement 2: 급등 모멘텀 감지

**User Story:** As a trader, I want the system to detect sudden large buying momentum, so that I can capture stocks with abrupt institutional interest before they peak.

#### Acceptance Criteria

1. WHEN one_day_net_buy is greater than or equal to 10000000000 (100억), THE Data_Collector SHALL mark the signal as having surge momentum
2. THE Data_Collector SHALL calculate one_day_net_buy as the sum of agency_net_buy and foreign_net_buy
3. WHEN a surge momentum signal is detected, THE Signal_Analyzer SHALL include the ticker in the candidate pool regardless of Consecutive_Buy_Days value
4. THE Signal_Analyzer SHALL assign a momentum score component of at least 15 points (out of 20 momentum points) to surge momentum signals

### Requirement 3: 가속 패턴 감지

**User Story:** As a trader, I want the system to detect accelerating buying patterns, so that I can identify stocks where institutional interest is intensifying.

#### Acceptance Criteria

1. THE Data_Collector SHALL calculate three_day_avg_net_buy as the average of one_day_net_buy over the most recent 3 trading days
2. WHEN one_day_net_buy is greater than or equal to 2 times three_day_avg_net_buy AND three_day_avg_net_buy is positive, THE Data_Collector SHALL mark the signal as having acceleration pattern
3. THE Signal_Analyzer SHALL assign a momentum score component of at least 12 points (out of 20 momentum points) to acceleration pattern signals
4. IF three_day_avg_net_buy is less than or equal to zero, THEN THE Data_Collector SHALL not mark the signal as having acceleration pattern

### Requirement 4: 거래량 급증 감지

**User Story:** As a trader, I want the system to detect volume surges, so that I can identify stocks with unusual trading activity that may precede price movements.

#### Acceptance Criteria

1. THE Yfinance_Client SHALL fetch daily volume data for each ticker
2. THE Data_Collector SHALL calculate volume_ratio as current day volume divided by 20-day average volume
3. WHEN volume_ratio is greater than or equal to 3.0, THE Signal_Analyzer SHALL assign a momentum score component of at least 10 points (out of 20 momentum points)
4. IF 20-day average volume is zero or unavailable, THEN THE Data_Collector SHALL set volume_ratio to NULL

### Requirement 5: RSI 지표 수집 및 평가

**User Story:** As a trader, I want the system to collect RSI indicators, so that I can avoid overbought stocks and identify oversold opportunities.

#### Acceptance Criteria

1. THE Yfinance_Client SHALL calculate 14-period RSI for each ticker using closing prices
2. THE Data_Collector SHALL store the RSI value in the rsi column with precision of 2 decimal places
3. WHEN rsi is between 30 and 70, THE Signal_Analyzer SHALL assign a technical score component of 5 points (out of 10 technical points)
4. WHEN rsi is less than 30, THE Signal_Analyzer SHALL assign a technical score component of 8 points (out of 10 technical points) for oversold opportunity
5. WHEN rsi is greater than 70, THE Signal_Analyzer SHALL assign a technical score component of 2 points (out of 10 technical points) for overbought warning
6. IF RSI calculation fails or insufficient data exists, THEN THE Data_Collector SHALL set rsi to NULL

### Requirement 6: 이동평균선 배열 수집 및 평가

**User Story:** As a trader, I want the system to evaluate moving average alignment, so that I can identify stocks in strong uptrends.

#### Acceptance Criteria

1. THE Yfinance_Client SHALL calculate 5-day, 20-day, and 60-day simple moving averages of closing prices
2. WHEN 5-day MA is greater than 20-day MA AND 20-day MA is greater than 60-day MA, THE Data_Collector SHALL set ma_alignment to "bullish"
3. WHEN 5-day MA is less than 20-day MA AND 20-day MA is less than 60-day MA, THE Data_Collector SHALL set ma_alignment to "bearish"
4. WHEN the moving averages do not satisfy bullish or bearish conditions, THE Data_Collector SHALL set ma_alignment to "neutral"
5. THE Signal_Analyzer SHALL assign a technical score component of 3 points (out of 10 technical points) when ma_alignment is "bullish"
6. THE Signal_Analyzer SHALL assign a technical score component of 0 points when ma_alignment is "bearish"
7. THE Signal_Analyzer SHALL assign a technical score component of 1 point when ma_alignment is "neutral"
8. IF moving average calculation fails or insufficient data exists, THEN THE Data_Collector SHALL set ma_alignment to NULL

### Requirement 7: 볼린저밴드 위치 수집 및 평가

**User Story:** As a trader, I want the system to track Bollinger Band positions, so that I can identify stocks at extreme price levels relative to their volatility.

#### Acceptance Criteria

1. THE Yfinance_Client SHALL calculate 20-period Bollinger Bands with 2 standard deviations
2. THE Data_Collector SHALL calculate bollinger_position as (current_price - lower_band) / (upper_band - lower_band)
3. THE Data_Collector SHALL store bollinger_position with precision of 3 decimal places
4. WHEN bollinger_position is between 0.3 and 0.7, THE Signal_Analyzer SHALL assign a technical score component of 1 point (out of 10 technical points)
5. WHEN bollinger_position is less than 0.2, THE Signal_Analyzer SHALL assign a technical score component of 2 points (out of 10 technical points) for potential bounce
6. WHEN bollinger_position is greater than 0.8, THE Signal_Analyzer SHALL assign a technical score component of 0 points for potential reversal risk
7. IF Bollinger Band calculation fails or upper_band equals lower_band, THEN THE Data_Collector SHALL set bollinger_position to NULL

### Requirement 8: 거래대금 수집 및 유동성 평가

**User Story:** As a trader, I want the system to track trading value, so that I can ensure recommended stocks have sufficient liquidity for execution.

#### Acceptance Criteria

1. THE Yfinance_Client SHALL fetch daily trading value in KRW for each ticker
2. THE Data_Collector SHALL store the trading value in the trading_value column
3. WHEN trading_value is greater than or equal to 10000000000 (100억 원), THE Signal_Analyzer SHALL assign a technical score component of 1 point (out of 10 technical points)
4. WHEN trading_value is less than 5000000000 (50억 원), THE Signal_Analyzer SHALL reduce the total score by 5 points as a liquidity penalty
5. IF trading value data is unavailable, THEN THE Data_Collector SHALL set trading_value to NULL

### Requirement 9: Yfinance 기술적 지표 수집 통합

**User Story:** As a system operator, I want the data collector to fetch technical indicators from yfinance during the intraday collection phase, so that momentum signals are available for AI analysis.

#### Acceptance Criteria

1. WHEN process_intraday is executed, THE Data_Collector SHALL invoke Yfinance_Client for each ticker in the signals dataset
2. THE Yfinance_Client SHALL fetch volume, RSI, moving averages, Bollinger Bands, and trading value in a single batch operation per ticker
3. THE Data_Collector SHALL update the Signals_Table with technical indicator values for each ticker
4. IF Yfinance_Client fails for a specific ticker, THEN THE Data_Collector SHALL log a warning and set all technical indicator columns to NULL for that ticker
5. THE Data_Collector SHALL not increase the number of KIS_API calls
6. THE Data_Collector SHALL complete technical indicator collection within 5 minutes for up to 200 tickers

### Requirement 10: 점수 체계 재조정

**User Story:** As a product owner, I want the scoring system to reflect both traditional supply-demand signals and momentum indicators, so that recommendations balance stability and opportunity.

#### Acceptance Criteria

1. THE Signal_Analyzer SHALL calculate the total score as a weighted sum with the following distribution: supply 40%, news 30%, macro 20%, technical 10%
2. THE Signal_Analyzer SHALL allocate the 40% supply score as follows: 20% for Consecutive_Buy_Days and 20% for momentum signals
3. THE Signal_Analyzer SHALL assign momentum points based on surge momentum (15 points), acceleration pattern (12 points), and volume surge (10 points) with a maximum of 20 points
4. THE Signal_Analyzer SHALL assign technical points based on RSI (up to 8 points), MA alignment (up to 3 points), Bollinger position (up to 2 points), and trading value (up to 1 point) with a maximum of 10 points
5. THE Signal_Analyzer SHALL normalize all component scores to fit within their allocated percentage ranges before summing
6. THE Signal_Analyzer SHALL round the final score to the nearest integer between 0 and 100

### Requirement 11: 모멘텀 신호 후보 풀 확장

**User Story:** As a trader, I want surge momentum stocks to be included in recommendations even without 3-day consecutive buying, so that I can capture sudden opportunities.

#### Acceptance Criteria

1. WHEN SignalAnalysisTask queries for new candidates, THE Signal_Analyzer SHALL include tickers where Consecutive_Buy_Days is less than 3 if one_day_net_buy is greater than or equal to 10000000000
2. THE Signal_Analyzer SHALL include tickers where Consecutive_Buy_Days is less than 3 if volume_ratio is greater than or equal to 3.0 AND one_day_net_buy is positive
3. THE Signal_Analyzer SHALL maintain the existing logic to include tickers where Consecutive_Buy_Days is greater than or equal to 3
4. THE Signal_Analyzer SHALL exclude holdings tickers from new candidates regardless of momentum signals

### Requirement 12: 하위 호환성 유지

**User Story:** As a system operator, I want existing functionality to remain intact, so that the system continues to work correctly during and after the migration.

#### Acceptance Criteria

1. THE System SHALL continue to calculate and store Consecutive_Buy_Days using the existing algorithm
2. THE Signal_Analyzer SHALL continue to support queries with min_consecutive parameter for backward compatibility
3. WHEN momentum indicator columns contain NULL values, THE Signal_Analyzer SHALL assign zero points for those indicators without failing
4. THE System SHALL pass all existing unit tests without modification to test assertions
5. THE System SHALL produce recommendations using the legacy scoring system when all momentum indicator columns are NULL

### Requirement 13: 모멘텀 신호 분석 도구 추가

**User Story:** As an AI agent, I want a tool to query momentum indicators, so that I can evaluate stocks using both traditional and momentum-based criteria.

#### Acceptance Criteria

1. THE System SHALL provide a MomentumQueryTool that accepts target_date and optional tickers parameters
2. WHEN MomentumQueryTool is invoked, THE System SHALL return one_day_net_buy, three_day_avg_net_buy, volume_ratio, rsi, ma_alignment, bollinger_position, and trading_value for each ticker
3. THE MomentumQueryTool SHALL return results in JSON format compatible with CrewAI tool output standards
4. THE Signal_Analyzer SHALL invoke MomentumQueryTool during SignalAnalysisTask to retrieve momentum data for candidate tickers
5. IF a ticker has NULL values for momentum indicators, THEN THE MomentumQueryTool SHALL include the ticker in results with NULL values explicitly shown

### Requirement 14: 에러 처리 및 로깅

**User Story:** As a system operator, I want comprehensive error handling and logging for momentum data collection, so that I can diagnose and resolve issues quickly.

#### Acceptance Criteria

1. WHEN Yfinance_Client fails to fetch data for a ticker, THE Data_Collector SHALL log a warning with the ticker symbol and error message
2. THE Data_Collector SHALL continue processing remaining tickers when a single ticker fails
3. WHEN technical indicator calculation produces invalid values (negative RSI, bollinger_position outside 0-1 range), THE Data_Collector SHALL log a warning and set the value to NULL
4. THE Data_Collector SHALL log the count of successfully processed tickers and failed tickers at the end of technical indicator collection
5. THE System SHALL include momentum indicator collection status in the process_intraday return payload

