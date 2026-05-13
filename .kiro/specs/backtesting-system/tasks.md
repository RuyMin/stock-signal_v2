# Implementation Plan: Backtesting System

## Overview

This plan implements a backtesting system that validates AI recommendation performance by comparing historical recommendations against actual stock price movements. The system consists of a new worker service (`worker-backtester`), database schema extensions, scheduler integration, and monitoring dashboards.

## Tasks

- [ ] 1. Set up database schema and migrations
  - [ ] 1.1 Create Alembic migration file for price_history and backtest_results tables
    - Create migration file `backend/migrations/versions/20260505_0001_add_backtesting_tables.py`
    - Define price_history table with columns: id, ticker, date, close_price, collected_at
    - Define backtest_results table with columns: id, recommendation_id, recommendation_close_price, return_3d, return_7d, return_14d, calculated_at
    - Add UNIQUE constraints, CHECK constraints, and foreign key relationships
    - Create indexes on (ticker, date) and (recommendation_id)
    - _Requirements: 1.1, 1.4, 1.7, 2.1, 2.6, 8.5_

  - [ ]* 1.2 Write property test for database schema constraints
    - **Property 3: Recommendations Table Immutability**
    - **Validates: Requirements 2.8**

- [ ] 2. Implement price fetcher module
  - [ ] 2.1 Create workers/backtester/price_fetcher.py with YFinance integration
    - Implement `fetch_prices_for_tickers()` with parallel async fetching (max 10 concurrent)
    - Implement `_fetch_one_ticker()` with retry logic and exponential backoff
    - Use `asyncio.to_thread()` to wrap synchronous yfinance calls
    - Handle rate limits with 60s, 120s, 240s backoff intervals
    - Return empty list for missing tickers (no KeyError)
    - _Requirements: 1.3, 1.5, 1.6, 6.1, 6.2, 8.1_

  - [ ] 2.2 Implement YFinance response parser
    - Parse "Close" column from DataFrame into close_price field
    - Convert pandas Timestamp to Python date objects
    - Handle timezone-aware timestamps (convert to KST)
    - Validate close_price is positive before storing
    - Return empty list for empty DataFrame (no exceptions)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 2.3 Write property test for YFinance data round-trip
    - **Property 2: YFinance Data Round-Trip**
    - **Validates: Requirements 7.8, 7.1, 7.2, 7.3**

  - [ ]* 2.4 Write unit tests for price fetcher edge cases
    - Test empty DataFrame handling
    - Test delisted stock handling
    - Test rate limit retry logic
    - Test invalid price values (NULL, negative, zero)
    - _Requirements: 1.5, 1.6, 6.1, 7.4, 7.5, 7.6_

- [ ] 3. Checkpoint - Ensure price fetcher tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Implement processor module for backtest calculations
  - [ ] 4.1 Create workers/backtester/processor.py with core calculation logic
    - Implement `run_daily_backtest()` function
    - Query recommendations from past 14 days
    - Fetch prices for all unique tickers
    - Store prices in price_history table (upsert with ON CONFLICT DO NOTHING)
    - Calculate returns for each recommendation
    - Store results in backtest_results table (upsert with ON CONFLICT UPDATE)
    - Return summary dict with counts
    - _Requirements: 1.2, 1.4, 1.7, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 8.6_

  - [ ] 4.2 Implement calculate_returns() function
    - Retrieve recommendation_close_price from price_history for target_trading_date
    - Calculate return_3d, return_7d, return_14d using formula: ((future_price - base_price) / base_price) * 100
    - Return NULL for periods where future date hasn't occurred
    - Return NULL for periods where price data is unavailable
    - Handle non-trading days by using next available trading day
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 9.1, 9.2, 9.5, 9.6_

  - [ ]* 4.3 Write property test for return calculation correctness
    - **Property 1: Return Calculation Correctness**
    - **Validates: Requirements 2.3**

  - [ ]* 4.4 Write property test for trading day calculation
    - **Property 12: Trading Day Calculation Accuracy**
    - **Validates: Requirements 9.5**

  - [ ]* 4.5 Write unit tests for return calculation edge cases
    - Test known input/output pairs
    - Test negative returns
    - Test NULL future prices
    - Test missing base prices
    - Test non-trading day handling
    - _Requirements: 2.4, 2.5, 6.6, 6.7, 9.6_

- [ ] 5. Implement report generator module
  - [ ] 5.1 Create workers/backtester/report_generator.py
    - Implement `generate_weekly_report()` function
    - Query backtest_results joined with recommendations for past 7 days
    - Calculate metrics by recommendation_type: win_rate, avg_return, MDD
    - Handle case where all returns are NULL (display "데이터 부족")
    - Return formatted Telegram message string
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.8_

  - [ ] 5.2 Implement format_report_message() function
    - Format metrics into Telegram message with table layout
    - Include: recommendation_type, count, win_rate, avg_return, MDD
    - Display overall MDD at the bottom
    - Use Korean text and emoji formatting
    - _Requirements: 4.6, 7.7_

  - [ ]* 5.3 Write property test for report metrics correctness
    - **Property 7: Report Metrics Correctness**
    - **Validates: Requirements 4.3, 4.4, 4.5**

  - [ ]* 5.4 Write property test for report date range accuracy
    - **Property 6: Report Date Range Accuracy**
    - **Validates: Requirements 4.2**

  - [ ]* 5.5 Write unit tests for report generation
    - Test report format completeness
    - Test "데이터 부족" case
    - Test metrics calculation with known values
    - _Requirements: 4.6, 4.8_

- [ ] 6. Checkpoint - Ensure core logic tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement Kafka event schemas and helpers
  - [ ] 7.1 Create workers/backtester/schemas/events.py
    - Define BacktestTriggerEvent dataclass
    - Define BacktestCompletedEvent dataclass
    - Define BacktestFailedEvent dataclass
    - Define BacktestReportCompletedEvent dataclass
    - _Requirements: 3.3, 3.4, 3.5, 4.9_

  - [ ] 7.2 Create workers/backtester/core/kafka_io.py
    - Implement Kafka producer helper for publishing events
    - Implement Kafka consumer helper for subscribing to triggers
    - Handle serialization/deserialization of event schemas
    - _Requirements: 3.3, 3.4, 3.5, 4.9_

  - [ ]* 7.3 Write property test for event publishing completeness
    - **Property 5: Event Publishing Completeness**
    - **Validates: Requirements 3.3, 3.4, 4.9**

- [ ] 8. Implement backtester worker main service
  - [ ] 8.1 Create workers/backtester/main.py entry point
    - Set up asyncpg connection pool (min 2, max 10 connections)
    - Set up Kafka consumer for stock.backtest.trigger and stock.backtest.report.trigger topics
    - Implement consumer loop to handle trigger events
    - Call run_daily_backtest() on stock.backtest.trigger
    - Call generate_and_send_weekly_report() on stock.backtest.report.trigger
    - Publish completion/failure events
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 4.1, 4.9, 8.4_

  - [ ] 8.2 Create workers/backtester/core/db.py
    - Implement asyncpg pool initialization
    - Implement connection health checks
    - _Requirements: 8.4_

  - [ ] 8.3 Create workers/backtester/core/logging.py
    - Set up structlog configuration
    - Implement structured logging for major operations
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 8.4 Write property test for job lifecycle management
    - **Property 4: Job Lifecycle Management**
    - **Validates: Requirements 3.6, 3.7**

  - [ ]* 8.5 Write integration test for full backtest flow
    - Test end-to-end daily backtest execution
    - Mock YFinance responses
    - Verify results stored in database
    - Verify job status updated
    - _Requirements: 3.1, 3.6, 3.7_

- [ ] 9. Implement error handling and resilience
  - [ ] 9.1 Add error recording to processor module
    - Implement record_error() function
    - Store errors in job_errors table with service="backtesting"
    - _Requirements: 6.4_

  - [ ] 9.2 Add circuit breaker logic to run_daily_backtest()
    - Check if >50% of tickers failed
    - Mark job as failed and send alert if threshold exceeded
    - _Requirements: 6.5_

  - [ ] 9.3 Add transaction rollback handling
    - Wrap price inserts in transactions
    - Rollback on database errors and continue processing
    - _Requirements: 6.3, 9.4_

  - [ ] 9.4 Add data validation checks
    - Verify recommendation_close_price is not NULL before calculating returns
    - Skip recommendations with NULL base price and log warning
    - Log warning if >20% of recommendations have NULL returns
    - _Requirements: 6.6, 6.7, 9.7_

  - [ ]* 9.5 Write property test for error recording consistency
    - **Property 10: Error Recording Consistency**
    - **Validates: Requirements 6.4**

  - [ ]* 9.6 Write unit tests for error handling
    - Test rate limit retry logic
    - Test individual ticker failure handling
    - Test database error rollback
    - Test circuit breaker triggering
    - _Requirements: 6.1, 6.2, 6.3, 6.5_

- [ ] 10. Extend scheduler service with backtest triggers
  - [ ] 10.1 Add trigger functions to scheduler/main.py
    - Implement trigger_backtest_daily() function
    - Implement trigger_backtest_report() function
    - Publish Kafka events to stock.backtest.trigger and stock.backtest.report.trigger
    - _Requirements: 3.1, 3.3, 4.1_

  - [ ] 10.2 Add cron jobs to scheduler
    - Add daily-backtest-trigger: CronTrigger(hour=6, minute=0, timezone=TZ_KST)
    - Add weekly-report-trigger: CronTrigger(day_of_week='sun', hour=9, minute=0, timezone=TZ_KST)
    - _Requirements: 3.1, 4.1_

  - [ ]* 10.3 Write unit tests for scheduler triggers
    - Test trigger_backtest_daily() publishes correct event
    - Test trigger_backtest_report() publishes correct event
    - Test cron schedule timing
    - _Requirements: 3.1, 3.3, 4.1_

- [ ] 11. Checkpoint - Ensure scheduler and error handling tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Implement Telegram report distribution
  - [ ] 12.1 Add send_weekly_report() function to report_generator.py
    - Query all active users from users table
    - Send formatted report to each user via Telegram Bot
    - Handle Telegram API failures with retry (up to 3 times)
    - Publish stock.backtest.report.completed event with recipient_count
    - _Requirements: 4.7, 4.9_

  - [ ]* 12.2 Write property test for report distribution completeness
    - **Property 9: Report Distribution Completeness**
    - **Validates: Requirements 4.7**

  - [ ]* 12.3 Write integration test for weekly report generation
    - Test end-to-end report generation and sending
    - Mock Telegram bot
    - Verify report sent to all active users
    - Verify report content format
    - _Requirements: 4.1, 4.6, 4.7, 4.9_

- [ ] 13. Implement monitoring and observability
  - [ ] 13.1 Add structured logging to all major operations
    - Log "backtest_started" with timestamp and recommendation_count
    - Log "price_collection_completed" with ticker_count and success_rate
    - Log "return_calculation_completed" with processed_count
    - Log "weekly_report_generated" with recipient_count
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ] 13.2 Add job progress tracking
    - Update job progress field at 0%, 25%, 50%, 75%, 100% during execution
    - _Requirements: 10.7_

  - [ ] 13.3 Add Prometheus metrics (optional)
    - Expose backtest_duration_seconds metric
    - Expose backtest_recommendations_total metric
    - Expose backtest_errors_total metric
    - _Requirements: 10.6_

  - [ ]* 13.4 Write property test for structured logging completeness
    - **Property 13: Structured Logging Completeness**
    - **Validates: Requirements 10.2, 10.3, 10.4, 10.5**

  - [ ]* 13.5 Write property test for job progress tracking
    - **Property 14: Job Progress Tracking**
    - **Validates: Requirements 10.7**

- [ ] 14. Create Grafana dashboard
  - [ ] 14.1 Create infra/grafana/dashboards/backtesting-performance.json
    - Add time series panel: Daily Win Rate by Type
    - Add bar chart panel: Average Return by Type (30 days)
    - Add line chart panel: Cumulative Return
    - Add stat panel: Overall Win Rate
    - Add stat panel: Total Backtested Recommendations
    - Add table panel: Recent Recommendations with Returns
    - Add dashboard variables: $recommendation_type, $__timeFilter
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 14.2 Test Grafana dashboard queries
    - Verify all panel queries return expected data
    - Test filtering by recommendation_type
    - Test time range filtering
    - _Requirements: 5.7_

- [ ] 15. Implement performance optimizations
  - [ ] 15.1 Add batch processing for large datasets
    - Process recommendations in batches of 100 when >500 total
    - Batch insert prices in groups of 50 records per transaction
    - _Requirements: 8.6, 8.7_

  - [ ] 15.2 Verify performance requirements
    - Ensure price collection for 100 tickers completes within 2 minutes
    - Ensure return calculations for 300 recommendations complete within 1 minute
    - Ensure daily backtest completes before 06:30 KST
    - _Requirements: 3.2, 8.2, 8.3_

  - [ ]* 15.3 Write property test for ticker query completeness
    - **Property 15: Ticker Query Completeness**
    - **Validates: Requirements 1.2**

  - [ ]* 15.4 Write property test for price fetch date range accuracy
    - **Property 16: Price Fetch Date Range Accuracy**
    - **Validates: Requirements 1.3**

  - [ ]* 15.5 Write property test for base price retrieval accuracy
    - **Property 17: Base Price Retrieval Accuracy**
    - **Validates: Requirements 2.2**

- [ ] 16. Implement data integrity checks
  - [ ] 16.1 Add backtest result update preservation logic
    - Only update NULL return values with newly available data
    - Preserve existing non-NULL values during recalculation
    - _Requirements: 9.3_

  - [ ]* 16.2 Write property test for backtest result update preservation
    - **Property 11: Backtest Result Update Preservation**
    - **Validates: Requirements 9.3**

  - [ ]* 16.3 Write unit tests for data integrity
    - Test target_trading_date validation
    - Test price_history data availability check
    - Test transaction atomicity
    - _Requirements: 9.1, 9.2, 9.4_

- [ ] 17. Create Docker and deployment configuration
  - [ ] 17.1 Create workers/backtester/Dockerfile
    - Use Python 3.11 base image
    - Install dependencies from requirements.txt
    - Set up entry point for main.py
    - _Requirements: deployment_

  - [ ] 17.2 Create workers/backtester/requirements.txt
    - Add yfinance, asyncpg, aiokafka, structlog, python-telegram-bot
    - Pin versions for reproducibility
    - _Requirements: deployment_

  - [ ] 17.3 Update docker-compose.yml to include backtester service
    - Add worker-backtester service definition
    - Configure environment variables (DB, Kafka, Telegram)
    - Set up service dependencies
    - _Requirements: deployment_

- [ ] 18. Final checkpoint - Run all tests and verify integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at key milestones
- Property tests validate universal correctness properties (17 total)
- Unit tests validate specific examples and edge cases
- Integration tests validate end-to-end flows
- The implementation uses Python with async/await patterns throughout
- All database operations use asyncpg for async PostgreSQL access
- Kafka integration uses aiokafka for async message handling
