# Requirements Document

## Introduction

백테스팅 시스템은 과거 AI 추천 데이터를 기반으로 전략 성과를 자동 분석하고 주간 리포트를 생성하는 시스템입니다. 현재 시스템은 매일 AI 추천을 생성하지만 추천의 실제 성과를 추적하고 분석하는 메커니즘이 없습니다. 이 시스템은 추천 전략의 유효성을 데이터 기반으로 검증하고, 사용자에게 투명한 성과 지표를 제공합니다.

## Glossary

- **Backtesting_System**: 과거 추천 데이터와 실제 가격 데이터를 비교하여 전략 성과를 계산하는 시스템
- **Price_History_Table**: 추천 종목의 일별 종가를 저장하는 데이터베이스 테이블
- **Backtest_Results_Table**: 각 추천의 기간별 수익률과 성과 지표를 저장하는 데이터베이스 테이블
- **Return_Period**: 수익률 계산 기간 (3일, 7일, 14일)
- **Win_Rate**: 수익률이 0% 이상인 추천의 비율
- **MDD**: Maximum Drawdown, 최대 낙폭 (peak 대비 최대 하락률)
- **Weekly_Report**: 지난 7일간의 추천 성과를 요약한 텔레그램 리포트
- **Recommendation_Type**: 추천 유형 (buy_hedge, watch, exit_alert)
- **YFinance**: Yahoo Finance API를 통한 주가 데이터 수집 라이브러리
- **Target_Trading_Date**: 추천 대상 거래일 (recommendations.target_trading_date)
- **Recommendation_Close_Price**: 추천일(target_trading_date)의 종가
- **Grafana_Dashboard**: 백테스트 성과 지표를 시각화하는 대시보드

## Requirements

### Requirement 1: 가격 데이터 수집

**User Story:** As a system, I want to collect daily closing prices for recommended stocks, so that I can calculate actual returns for backtesting

#### Acceptance Criteria

1. THE Backtesting_System SHALL create a Price_History_Table with columns (id, ticker, date, close_price, collected_at)
2. WHEN a backtest is triggered, THE Backtesting_System SHALL query all unique tickers from recommendations table for the past 14 days
3. FOR EACH ticker, THE Backtesting_System SHALL fetch daily closing prices using YFinance for the date range from target_trading_date to current date
4. THE Backtesting_System SHALL store fetched prices in Price_History_Table with UNIQUE constraint on (ticker, date)
5. IF YFinance returns no data for a ticker, THEN THE Backtesting_System SHALL log a warning and skip that ticker
6. THE Backtesting_System SHALL handle delisted stocks by skipping them without raising errors
7. THE Backtesting_System SHALL use ON CONFLICT DO NOTHING when inserting prices to handle duplicate date entries

### Requirement 2: 백테스트 결과 계산

**User Story:** As a system, I want to calculate returns for each recommendation at multiple time horizons, so that I can measure strategy performance

#### Acceptance Criteria

1. THE Backtesting_System SHALL create a Backtest_Results_Table with columns (id, recommendation_id, recommendation_close_price, return_3d, return_7d, return_14d, calculated_at)
2. FOR EACH recommendation in the past 14 days, THE Backtesting_System SHALL retrieve the Recommendation_Close_Price from Price_History_Table for Target_Trading_Date
3. FOR EACH Return_Period (3, 7, 14 days), THE Backtesting_System SHALL calculate return as ((future_price - Recommendation_Close_Price) / Recommendation_Close_Price) * 100
4. IF the future date has not occurred yet, THEN THE Backtesting_System SHALL store NULL for that Return_Period
5. IF Price_History_Table has no data for a required date, THEN THE Backtesting_System SHALL store NULL for that Return_Period
6. THE Backtesting_System SHALL store all calculated returns in Backtest_Results_Table with UNIQUE constraint on recommendation_id
7. THE Backtesting_System SHALL use ON CONFLICT UPDATE to overwrite existing results when recalculating
8. THE Backtesting_System SHALL preserve the original recommendations table without modifications (read-only access)

### Requirement 3: 백테스트 스케줄링

**User Story:** As a system, I want to run backtests automatically every morning, so that performance metrics are updated daily

#### Acceptance Criteria

1. THE Backtesting_System SHALL execute backtest calculations daily at 06:00 KST
2. THE Backtesting_System SHALL complete execution before 06:30 KST (premarket data collection time)
3. WHEN backtest execution starts, THE Backtesting_System SHALL publish a Kafka message to topic "stock.backtest.started"
4. WHEN backtest execution completes successfully, THE Backtesting_System SHALL publish a Kafka message to topic "stock.backtest.completed" with result counts
5. IF backtest execution fails, THEN THE Backtesting_System SHALL publish a Kafka message to topic "stock.backtest.failed" with error details
6. THE Backtesting_System SHALL create a Job record in jobs table with job_type "backtest_daily"
7. THE Backtesting_System SHALL update Job status to "completed" or "failed" upon execution completion

### Requirement 4: 주간 리포트 생성

**User Story:** As a user, I want to receive a weekly performance summary via Telegram, so that I can evaluate recommendation quality

#### Acceptance Criteria

1. THE Backtesting_System SHALL generate a Weekly_Report every Sunday at 09:00 KST
2. THE Weekly_Report SHALL include recommendations from the past 7 days (target_trading_date basis)
3. FOR EACH Recommendation_Type, THE Weekly_Report SHALL calculate Win_Rate as (count of return_3d > 0) / (count of non-NULL return_3d) * 100
4. FOR EACH Recommendation_Type, THE Weekly_Report SHALL calculate average return_3d for all non-NULL values
5. THE Weekly_Report SHALL calculate overall MDD as the maximum negative return across all recommendations in the period
6. THE Weekly_Report SHALL format results as a Telegram message with table layout showing type, count, win_rate, avg_return, and MDD
7. THE Backtesting_System SHALL send the Weekly_Report to all active users via Telegram Bot
8. IF there are no completed recommendations (all returns are NULL), THEN THE Weekly_Report SHALL display "데이터 부족" message
9. WHEN Weekly_Report is sent successfully, THE Backtesting_System SHALL publish a Kafka message to topic "stock.backtest.report.completed"

### Requirement 5: Grafana 대시보드 통합

**User Story:** As a user, I want to visualize backtest metrics in Grafana, so that I can monitor strategy performance over time

#### Acceptance Criteria

1. THE Backtesting_System SHALL expose backtest metrics queryable by Grafana via PostgreSQL datasource
2. THE Grafana_Dashboard SHALL display a time series panel showing daily Win_Rate for each Recommendation_Type
3. THE Grafana_Dashboard SHALL display a bar chart panel showing average return by Recommendation_Type for the past 30 days
4. THE Grafana_Dashboard SHALL display a line chart panel showing cumulative return over time (sum of all return_3d values)
5. THE Grafana_Dashboard SHALL display a stat panel showing current overall Win_Rate
6. THE Grafana_Dashboard SHALL display a stat panel showing total number of backtested recommendations
7. THE Grafana_Dashboard SHALL allow filtering by date range and Recommendation_Type

### Requirement 6: 에러 처리 및 복원력

**User Story:** As a system, I want to handle data collection failures gracefully, so that partial failures do not block the entire backtest

#### Acceptance Criteria

1. IF YFinance rate limit is exceeded, THEN THE Backtesting_System SHALL wait 60 seconds and retry up to 3 times
2. IF a ticker fetch fails after retries, THEN THE Backtesting_System SHALL log the error and continue processing remaining tickers
3. IF Price_History_Table insert fails due to database error, THEN THE Backtesting_System SHALL rollback that ticker's transaction and continue
4. THE Backtesting_System SHALL record all errors in job_errors table with service "backtesting"
5. WHEN more than 50% of tickers fail to fetch prices, THEN THE Backtesting_System SHALL mark the Job as "failed" and send alert
6. THE Backtesting_System SHALL validate that Recommendation_Close_Price is not NULL before calculating returns
7. IF Recommendation_Close_Price is NULL, THEN THE Backtesting_System SHALL skip that recommendation and log a warning

### Requirement 7: 파서 및 데이터 변환

**User Story:** As a system, I want to parse YFinance responses correctly, so that price data is accurately stored

#### Acceptance Criteria

1. THE YFinance_Parser SHALL parse the "Close" column from YFinance DataFrame into close_price field
2. THE YFinance_Parser SHALL convert pandas Timestamp index to Python date objects
3. THE YFinance_Parser SHALL handle timezone-aware timestamps by converting to KST
4. IF YFinance returns empty DataFrame, THEN THE YFinance_Parser SHALL return an empty list without raising exceptions
5. THE YFinance_Parser SHALL validate that close_price is a positive number before storing
6. IF close_price is NULL or non-positive, THEN THE YFinance_Parser SHALL skip that date entry and log a warning
7. THE Pretty_Printer SHALL format Backtest_Results_Table data into human-readable Weekly_Report format
8. FOR ALL valid price data, parsing YFinance response then formatting then parsing SHALL produce equivalent data (round-trip property)

### Requirement 8: 성능 및 확장성

**User Story:** As a system, I want to process backtests efficiently, so that daily execution completes within time constraints

#### Acceptance Criteria

1. THE Backtesting_System SHALL fetch prices for all tickers in parallel using asyncio with maximum 10 concurrent requests
2. THE Backtesting_System SHALL complete price collection for 100 tickers within 2 minutes
3. THE Backtesting_System SHALL complete return calculations for 300 recommendations within 1 minute
4. THE Backtesting_System SHALL use database connection pooling with minimum 2 and maximum 10 connections
5. THE Backtesting_System SHALL create indexes on Price_History_Table (ticker, date) and Backtest_Results_Table (recommendation_id)
6. THE Backtesting_System SHALL batch insert prices in groups of 50 records per transaction
7. WHEN processing more than 500 recommendations, THE Backtesting_System SHALL process them in batches of 100

### Requirement 9: 데이터 무결성

**User Story:** As a system, I want to ensure data consistency, so that backtest results are reliable

#### Acceptance Criteria

1. THE Backtesting_System SHALL verify that recommendations.target_trading_date exists before calculating returns
2. THE Backtesting_System SHALL verify that Price_History_Table contains data for Target_Trading_Date before calculating returns
3. IF a recommendation has existing backtest results, THEN THE Backtesting_System SHALL update only NULL return values with newly available data
4. THE Backtesting_System SHALL use database transactions when updating Backtest_Results_Table to ensure atomicity
5. THE Backtesting_System SHALL validate that return_3d calculation date is exactly 3 trading days after Target_Trading_Date
6. IF the calculated date falls on a non-trading day, THEN THE Backtesting_System SHALL use the next available trading day's price
7. THE Backtesting_System SHALL log a warning if more than 20% of recommendations have NULL returns after backtest execution

### Requirement 10: 모니터링 및 관찰성

**User Story:** As a developer, I want to monitor backtest execution, so that I can detect and diagnose issues quickly

#### Acceptance Criteria

1. THE Backtesting_System SHALL log structured events using structlog for all major operations
2. WHEN backtest starts, THE Backtesting_System SHALL log "backtest_started" with timestamp and recommendation_count
3. WHEN price collection completes, THE Backtesting_System SHALL log "price_collection_completed" with ticker_count and success_rate
4. WHEN return calculation completes, THE Backtesting_System SHALL log "return_calculation_completed" with processed_count
5. WHEN Weekly_Report is generated, THE Backtesting_System SHALL log "weekly_report_generated" with recipient_count
6. THE Backtesting_System SHALL expose Prometheus metrics for backtest_duration_seconds, backtest_recommendations_total, and backtest_errors_total
7. THE Backtesting_System SHALL update Job progress field incrementally during execution (0%, 25%, 50%, 75%, 100%)
