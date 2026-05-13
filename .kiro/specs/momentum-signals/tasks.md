# Implementation Plan: Momentum Signals

## Overview

본 구현 계획은 stock-signal 시스템에 모멘텀 기반 신호 수집 및 평가 기능을 추가한다. 7개 단계로 구성되며, 각 단계는 데이터베이스 스키마 확장, yfinance 클라이언트 확장, 데이터 수집기 통합, CrewAI 도구 구현, 에이전트 및 태스크 업데이트, 통합 테스트, 배포 순으로 진행된다.

## Tasks

- [x] 1. Phase 1: Database Schema Extension
  - [x] 1.1 Create Alembic migration for momentum indicators
    - Create migration file `backend/migrations/versions/YYYYMMDD_0001_add_momentum_indicators.py`
    - Add 7 new columns: one_day_net_buy, three_day_avg_net_buy, volume_ratio, rsi, ma_alignment, bollinger_position, trading_value
    - All columns nullable with default NULL
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_
  
  - [x] 1.2 Update SignalSnapshot Pydantic model
    - Add 7 new optional fields to `shared/schemas/signals.py`
    - Ensure backward compatibility with NULL values
    - _Requirements: 1.9, 12.3_
  
  - [ ]* 1.3 Write property test for schema migration
    - **Property 18: NULL momentum indicator handling**
    - **Validates: Requirements 12.3, 12.5**
  
  - [x] 1.4 Test migration on staging database
    - Run migration up and down
    - Verify no data loss
    - Verify existing queries still work
    - _Requirements: 1.8, 12.4_

- [x] 2. Phase 2: Yfinance Client Extension
  - [x] 2.1 Create TechnicalIndicators dataclass
    - Implement dataclass in `workers/data_collector/clients/yfinance_client.py`
    - Include all fields: ticker, date, volume, volume_20d_avg, volume_ratio, rsi, ma_5d, ma_20d, ma_60d, ma_alignment, bb_upper, bb_lower, bb_position, trading_value
    - _Requirements: 9.2_
  
  - [x] 2.2 Implement fetch_technical_indicators function
    - Fetch 60 days of historical data from yfinance
    - Use asyncio.to_thread for async wrapper
    - Return TechnicalIndicators with NULL for failures
    - _Requirements: 9.1, 9.2, 9.4_
  
  - [x] 2.3 Implement RSI calculation
    - Use 14-period Wilder's smoothing method
    - Store with 2 decimal precision
    - Return NULL if insufficient data or calculation fails
    - _Requirements: 5.1, 5.2, 5.6_
  
  - [ ]* 2.4 Write property test for RSI calculation
    - **Property 5: RSI precision constraint**
    - **Validates: Requirements 5.2**
  
  - [x] 2.4 Implement moving average calculations
    - Calculate 5-day, 20-day, 60-day simple moving averages
    - Use pandas for efficient calculation
    - Return NULL if insufficient data
    - _Requirements: 6.1, 6.8_
  
  - [x] 2.5 Implement MA alignment classification
    - Classify as "bullish" when MA5 > MA20 > MA60
    - Classify as "bearish" when MA5 < MA20 < MA60
    - Classify as "neutral" otherwise
    - Return NULL if calculation fails
    - _Requirements: 6.2, 6.3, 6.4, 6.8_
  
  - [ ]* 2.6 Write property test for MA alignment classification
    - **Property 7: MA alignment classification**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.8**
  
  - [x] 2.7 Implement Bollinger Bands calculation
    - Calculate 20-period Bollinger Bands with 2 standard deviations
    - Calculate position as (price - lower) / (upper - lower)
    - Store with 3 decimal precision
    - Return NULL if upper equals lower or calculation fails
    - _Requirements: 7.1, 7.2, 7.3, 7.7_
  
  - [ ]* 2.8 Write property test for Bollinger position calculation
    - **Property 4: Bollinger position calculation**
    - **Validates: Requirements 7.2, 7.7**
  
  - [ ]* 2.9 Write property test for Bollinger position precision
    - **Property 6: Bollinger position precision and range constraint**
    - **Validates: Requirements 7.3**
  
  - [x] 2.10 Implement volume ratio calculation
    - Calculate current volume / 20-day average volume
    - Return NULL if 20-day average is zero or unavailable
    - _Requirements: 4.2, 4.4_
  
  - [ ]* 2.11 Write property test for volume ratio calculation
    - **Property 3: Volume ratio calculation**
    - **Validates: Requirements 4.2, 4.4**
  
  - [x] 2.12 Implement trading value fetching
    - Fetch daily trading value in KRW from yfinance
    - Return NULL if unavailable
    - _Requirements: 8.1, 8.5_
  
  - [ ]* 2.13 Write unit tests for error handling
    - Test insufficient data scenarios
    - Test API timeout scenarios
    - Test invalid ticker symbols
    - Verify NULL fallbacks
    - _Requirements: 9.4, 14.1, 14.2_

- [x] 3. Checkpoint - Ensure yfinance client tests pass
  - 51 tests passed (data-collector). docker-compose.test.yml에 test_yfinance_technical_indicators.py 등록 완료.

- [x] 4. Phase 3: Data Collector Integration
  - [x] 4.1 Implement _calculate_momentum_indicators helper
    - Calculate one_day_net_buy as agency_net_buy + foreign_net_buy
    - Query last 3 days of one_day_net_buy from database
    - Calculate three_day_avg_net_buy as average of 3 days
    - Return (one_day_net_buy, three_day_avg_net_buy)
    - _Requirements: 2.2, 3.1_
  
  - [ ]* 4.2 Write property test for one_day_net_buy calculation
    - **Property 1: One-day net buy calculation**
    - **Validates: Requirements 2.2**
  
  - [ ]* 4.3 Write property test for three_day_avg_net_buy calculation
    - **Property 2: Three-day average net buy calculation**
    - **Validates: Requirements 3.1**
  
  - [x] 4.4 Implement _fetch_and_store_technical_indicators helper
    - Accept pool, tickers list, target_date
    - Use asyncio.gather with semaphore for parallel fetching
    - Call fetch_technical_indicators for each ticker
    - Update signals table with technical indicators
    - Log warnings for failures
    - Return (success_count, failure_count)
    - _Requirements: 9.2, 9.4, 14.1, 14.2_
  
  - [ ]* 4.5 Write property test for technical indicator failure isolation
    - **Property 22: Technical indicator fetch failure isolation**
    - **Validates: Requirements 9.4, 14.2**
  
  - [x] 4.6 Modify process_intraday function
    - After calculating consecutive_buy_days, call _calculate_momentum_indicators
    - After storing supply data, call _fetch_and_store_technical_indicators
    - Update signals table INSERT/UPDATE to include all new columns
    - Update return payload with technical_indicators_count and technical_indicators_failed
    - Add structured logging for collection status
    - _Requirements: 9.1, 9.3, 9.6, 14.4, 14.5_
  
  - [ ]* 4.7 Write integration test for process_intraday
    - Test full intraday flow with mocked yfinance
    - Verify all columns populated correctly
    - Verify error handling for partial failures
    - _Requirements: 9.1, 9.3, 9.4_
  
  - [ ]* 4.8 Write property test for invalid technical indicator handling
    - **Property 23: Invalid technical indicator value handling**
    - **Validates: Requirements 14.3**

- [ ] 5. Checkpoint - Ensure data collector tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Phase 4: CrewAI MomentumQueryTool
  - [x] 6.1 Create MomentumQueryInput schema
    - Define Pydantic model with target_date and optional tickers
    - Add field descriptions in Korean
    - _Requirements: 13.1_
  
  - [x] 6.2 Implement MomentumQueryTool class
    - Inherit from VibeBaseTool
    - Set name to "momentum_query"
    - Set description in Korean (one_day_net_buy 단위는 원/KRW 명시)
    - Set args_schema to MomentumQueryInput
    - _Requirements: 13.1_
  
  - [x] 6.3 Implement _run method for MomentumQueryTool
    - Query signals table for target_date
    - Filter by tickers if provided
    - Select one_day_net_buy, three_day_avg_net_buy, volume_ratio, rsi, ma_alignment, bollinger_position, trading_value
    - Return JSON with count and items array (ORDER BY one_day_net_buy DESC)
    - Decimal → float 변환 (JSON 직렬화), NULL 값 그대로 노출
    - _Requirements: 13.2, 13.3, 13.5_
  
  - [ ]* 6.4 Write property test for MomentumQueryTool output completeness
    - **Property 20: MomentumQueryTool output completeness**
    - **Validates: Requirements 13.2, 13.5**
  
  - [ ]* 6.5 Write property test for MomentumQueryTool JSON format
    - **Property 21: MomentumQueryTool JSON format**
    - **Validates: Requirements 13.3**
  
  - [ ]* 6.6 Write unit tests for MomentumQueryTool
    - Test with valid date and tickers
    - Test with invalid date format
    - Test with empty results
    - Test with NULL values in database
    - _Requirements: 13.1, 13.2, 13.3, 13.5_

- [x] 7. Phase 5: CrewAI Agent and Task Updates
  - [x] 7.1 Update SignalAnalyzerAgent
    - Add MomentumQueryTool to tools list (3개 도구: signal/momentum/holdings)
    - Update goal to include momentum logic (3가지 OR 결합: 연속/급등/거래량 급증)
    - Update backstory to mention momentum indicators (RSI/이평선/볼린저밴드)
    - _Requirements: 13.4_
  
  - [x] 7.2 Update SignalAnalysisTask description
    - Add step to query momentum indicators using MomentumQueryTool
    - Update candidate pool expansion logic
    - Include tickers with consecutive_buy_days >= 3
    - Include tickers with one_day_net_buy >= 10B (원/KRW)
    - Include tickers with volume_ratio >= 3.0 AND one_day_net_buy > 0
    - Exclude holdings from new candidates
    - 단위 주의 명시: momentum 컬럼은 원(KRW), signal_query는 주식 수
    - _Requirements: 2.3, 11.1, 11.2, 11.3, 11.4_
  
  - [ ]* 7.3 Write property test for candidate pool expansion
    - **Property 10: Candidate pool expansion**
    - **Validates: Requirements 2.3, 11.1, 11.2, 11.3, 11.4**
  
  - [x] 7.4 Implement surge momentum detection logic
    - Mark signal as surge momentum when one_day_net_buy >= 10B
    - _Requirements: 2.1_
  
  - [ ]* 7.5 Write property test for surge momentum detection
    - **Property 8: Surge momentum detection**
    - **Validates: Requirements 2.1**
  
  - [x] 7.6 Implement acceleration pattern detection logic
    - Mark signal as acceleration when one_day_net_buy >= 2 * three_day_avg_net_buy AND three_day_avg_net_buy > 0
    - Do not mark when three_day_avg_net_buy <= 0
    - _Requirements: 3.2, 3.4_
  
  - [ ]* 7.7 Write property test for acceleration pattern detection
    - **Property 9: Acceleration pattern detection**
    - **Validates: Requirements 3.2, 3.4**
  
  - [x] 7.8 Implement momentum score calculation
    - Assign 15 points for surge momentum
    - Assign 12 points for acceleration pattern
    - Assign 10 points for volume surge (volume_ratio >= 3.0)
    - Take maximum of the three, cap at 20 points
    - _Requirements: 2.4, 3.3, 4.3, 10.3_
  
  - [ ]* 7.9 Write property test for momentum score calculation
    - **Property 15: Momentum score calculation**
    - **Validates: Requirements 10.3**
  
  - [x] 7.10 Implement RSI scoring logic
    - Assign 8 points when RSI < 30
    - Assign 5 points when 30 <= RSI <= 70
    - Assign 2 points when RSI > 70
    - Assign 0 points when RSI is NULL
    - _Requirements: 5.3, 5.4, 5.5_
  
  - [ ]* 7.11 Write property test for RSI scoring
    - **Property 11: RSI scoring**
    - **Validates: Requirements 5.3, 5.4, 5.5**
  
  - [x] 7.12 Implement MA alignment scoring logic
    - Assign 3 points when ma_alignment is "bullish"
    - Assign 1 point when ma_alignment is "neutral"
    - Assign 0 points when ma_alignment is "bearish"
    - Assign 0 points when ma_alignment is NULL
    - _Requirements: 6.5, 6.6, 6.7_
  
  - [ ]* 7.13 Write property test for MA alignment scoring
    - **Property 12: MA alignment scoring**
    - **Validates: Requirements 6.5, 6.6, 6.7**
  
  - [x] 7.14 Implement Bollinger position scoring logic
    - Assign 2 points when bollinger_position < 0.2
    - Assign 1 point when 0.3 <= bollinger_position <= 0.7
    - Assign 0 points when bollinger_position > 0.8
    - Assign 0 points when bollinger_position is NULL
    - _Requirements: 7.4, 7.5, 7.6_
  
  - [ ]* 7.15 Write property test for Bollinger position scoring
    - **Property 13: Bollinger position scoring**
    - **Validates: Requirements 7.4, 7.5, 7.6**
  
  - [x] 7.16 Implement trading value scoring and penalty logic
    - Assign 1 point when trading_value >= 10B
    - Assign 0 points when trading_value < 10B or NULL
    - Apply -5 point penalty when trading_value < 5B
    - _Requirements: 8.3, 8.4_
  
  - [ ]* 7.17 Write property test for trading value scoring
    - **Property 14: Trading value scoring and penalty**
    - **Validates: Requirements 8.3, 8.4**
  
  - [x] 7.18 Implement technical score calculation
    - Sum RSI score (0-8) + MA score (0-3) + Bollinger score (0-2) + trading value score (0-1)
    - Subtract liquidity penalty (5 points if trading_value < 5B)
    - Cap at maximum 10 points
    - _Requirements: 10.4_
  
  - [ ]* 7.19 Write property test for technical score calculation
    - **Property 16: Technical score calculation**
    - **Validates: Requirements 10.4**
  
  - [x] 7.20 Update SynthesisTask scoring system
    - Update scoring weights: supply 40%, news 30%, macro 20%, technical 10%
    - Split supply score: consecutive_buy_days 20% + momentum 20%
    - Normalize all component scores to fit allocated percentage ranges
    - Sum all components
    - Round final score to integer in range [0, 100]
    - _Requirements: 10.1, 10.2, 10.5, 10.6_
  
  - [ ]* 7.21 Write property test for total score calculation
    - **Property 17: Total score calculation and normalization**
    - **Validates: Requirements 10.1, 10.2, 10.5, 10.6**
  
  - [ ]* 7.22 Write property test for backward compatibility
    - **Property 19: Consecutive buy days backward compatibility**
    - **Validates: Requirements 12.1**

- [ ] 8. Checkpoint - Ensure CrewAI scoring tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Phase 6: Integration and Testing
  - [ ] 9.1 Run end-to-end test on staging environment
    - Deploy all services to staging
    - Trigger intraday collection
    - Verify signals table populated with momentum indicators
    - Trigger premarket collection
    - Verify CrewAI generates recommendations with new scoring
    - _Requirements: 9.1, 9.3, 13.4_
  
  - [ ]* 9.2 Run backward compatibility tests
    - Verify existing unit tests pass without modification
    - Verify recommendations generated when all momentum indicators are NULL
    - Verify legacy scoring system used when momentum indicators are NULL
    - _Requirements: 12.2, 12.4, 12.5_
  
  - [ ]* 9.3 Run performance tests
    - Test with 200 tickers
    - Verify collection completes within 5 minutes
    - Measure average fetch time per ticker
    - Measure success and failure rates
    - _Requirements: 9.6_
  
  - [ ] 9.4 Review and fix bugs
    - Address any issues found in integration testing
    - Optimize performance if needed
    - Update error handling if needed
    - _Requirements: 14.1, 14.2, 14.3, 14.4_
  
  - [ ] 9.5 Update documentation
    - Update RUNBOOK.md with new features
    - Document new scoring system
    - Document new momentum indicators
    - Document rollback procedures
    - _Requirements: All_

- [ ] 10. Phase 7: Deployment
  - [ ] 10.1 Deploy database migration to production
    - Run Alembic migration on production database
    - Verify migration success
    - Verify no data loss
    - _Requirements: 1.8_
  
  - [ ] 10.2 Deploy updated services
    - Deploy data collector worker
    - Deploy CrewAI service
    - Verify services start successfully
    - _Requirements: All_
  
  - [ ] 10.3 Monitor logs and metrics
    - Monitor data collection logs for errors
    - Monitor technical indicator collection success rate
    - Monitor recommendation generation
    - Monitor scoring distribution
    - _Requirements: 14.4, 14.5_
  
  - [ ] 10.4 Verify data collection
    - Wait for next intraday collection (16:30 KST)
    - Verify signals table populated with momentum indicators
    - Verify no errors in logs
    - _Requirements: 9.1, 9.3_
  
  - [ ] 10.5 Verify recommendation generation
    - Wait for next premarket collection (06:30 KST)
    - Verify recommendations generated with new scoring
    - Verify momentum indicators used in scoring
    - Verify candidate pool includes momentum signals
    - _Requirements: 2.3, 10.1, 11.1, 11.2, 11.3_
  
  - [ ] 10.6 Update monitoring dashboards
    - Add momentum collection metrics
    - Add scoring distribution metrics
    - Add alert rules for collection failures
    - _Requirements: 14.4, 14.5_

- [ ] 11. Final checkpoint - Production verification complete
  - Ensure all production verification steps complete, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at key milestones
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All property tests should use hypothesis library with minimum 100 iterations
- Integration tests verify end-to-end flows
- Performance tests ensure 200 tickers complete within 5 minutes
- Backward compatibility is maintained throughout implementation
