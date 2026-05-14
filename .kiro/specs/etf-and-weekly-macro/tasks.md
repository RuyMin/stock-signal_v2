# Implementation Plan: ETF Policy & Weekly Macro Report

## Overview

7개 단계로 구성. Phase 1~3은 일일 사이클 ETF 분리(즉시 효과), Phase 4~6은 주간 매크로 리포트 추가(신규 기능). Phase 7 배포.

## Tasks

- [x] 1. Phase 1: Schema & Instrument Type Inference
  - [x] 1.1 Implement `infer_instrument_type(name)` in `shared/schemas/holdings.py` (별도 utils/ 디렉토리 만들지 않고 도메인 모듈에 통합)
    - Pure function (no DB dependency)
    - Brand pattern detection (KODEX/TIGER/KBSTAR/ARIRANG/KOSEF/HANARO/ETN/ACE/RISE)
    - Index sub-classification (S&P/KOSPI/KOSDAQ/NASDAQ/DOW/CSI/MSCI/지수 keyword)
    - Returns 'single_stock' | 'index_etf' | 'sector_etf'
    - _Requirements: 2.1, 2.3, 2.4_

  - [x] 1.2 Unit tests for `infer_instrument_type` (tests/unit/backend/test_instrument_type.py — 22 cases)
    - Table-driven: 삼성전자, 코리안리, KODEX S&P500, TIGER 2차전지, KBSTAR US달러, ARIRANG, None, "", 소문자
    - Property test: 결정론적 + 같은 입력 → 같은 출력
    - _Requirements: 8.1_

  - [x] 1.3 Alembic migration `20260514_0001_add_instrument_type.py` (dev DB upgrade 성공, 379800 → index_etf 백필 확인)
    - upgrade: ALTER TABLE holdings ADD COLUMN instrument_type VARCHAR(20) NOT NULL DEFAULT 'single_stock'
    - Index 추가 (WHERE instrument_type != 'single_stock')
    - Backfill: 기존 row를 `infer_instrument_type(name)` 결과로 UPDATE
    - downgrade: DROP COLUMN
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.4 Migration upgrade + 백필 검증 (dev DB에 379800 KODEX→index_etf 분류 확인. downgrade는 운영 데이터 보호 위해 dev에서 시도 안 함)
    - dev DB에 fixture row INSERT (KODEX S&P500, 삼성전자 등) → upgrade → instrument_type 채워졌는지 확인 → downgrade → 컬럼 사라짐 확인
    - _Requirements: 8.4_

  - [ ] 1.5 New table `weekly_macro_reports`
    - 같은 migration에 포함 또는 별도 migration
    - Columns: id, week_start, week_end, job_id, macro_summary, macro_values, etf_evaluations, generated_at
    - UNIQUE(week_start) for idempotency
    - _Requirements: 5.1, 6.1, 7.4_

- [x] 2. Phase 2: 등록 시 자동 분류 + 재평가
  - [x] 2.1 backend `holdings` 추가/수정 시 instrument_type 자동 설정 (backend/routers/holdings.py add_holding)
    - `POST /holdings/{ticker}` 또는 `/start` listener 호출 경로에서 name 알려진 직후 infer
    - 사용자 명시적 override 인자 받을 수 있도록 schema 확장 (optional)
    - _Requirements: 2.1, 2.5_

  - [x] 2.2 `_fill_holding_names` 재평가 로직 (workers/data_collector/processor.py — UPDATE에 instrument_type 포함, single_stock에서 시작한 경우만)
    - workers/data_collector/processor.py: name이 KIS로 채워질 때 현재 instrument_type='single_stock' 행에 한해 재평가
    - 매칭되는 ETF 패턴이면 UPDATE
    - _Requirements: 2.2_

  - [x] 2.3 shared/schemas/holdings.py에 instrument_type 필드 추가 + InstrumentType Literal
    - Literal['single_stock', 'index_etf', 'sector_etf']
    - default='single_stock'
    - _Requirements: 1.2_

  - [-] 2.4 backend 단위 테스트 — instrument_type 자동 설정 검증 (infer_instrument_type 22 cases로 커버, holdings 통합 테스트는 후속)
    - /holdings 추가 + name="KODEX 미국S&P500" → DB row instrument_type='index_etf'
    - 단일주 등록 → 'single_stock'
    - _Requirements: 8.1_

- [x] 3. Phase 3: 일일 추천 사이클 ETF 제외
  - [x] 3.1 `HoldingsQueryTool` SQL 필터 추가 (WHERE instrument_type = 'single_stock')
    - `WHERE instrument_type = 'single_stock'`
    - description 업데이트 (ETF는 weekly 리포트로 처리됨 명시)
    - _Requirements: 3.1_

  - [-] 3.2 SignalAnalysisTask description 갱신 (HoldingsQueryTool description에 ETF 제외 명시로 대체 — LLM 컨텍스트에 충분)
    - "보유 종목 평가는 단일주에 한정" 명시
    - _Requirements: 3.1_

  - [x] 3.3 Notifier daily fan-out에 ETF 방어 필터 추가 (workers/telegram_notifier/processor.py — user_tickers 조회 시 instrument_type 필터)
    - 사용자별 holdings 매칭 시 instrument_type != 'single_stock' 제외
    - logging: `etf_filtered_from_daily_cycle`
    - _Requirements: 3.2, 3.4, 10.2_

  - [x] 3.4 crewai 단위 테스트 — HoldingsQueryTool 필터 (HoldingFactory에 instrument_type 인자 + test_holdings_query_filters_etf 추가)
    - HoldingFactory에 ETF 인자 추가
    - test_crew_008 보강: ETF 등록 → tool 결과에 포함 안 됨
    - _Requirements: 8.2_

  - [x] 3.5 통합 검증 — manual premarket(job 33333333) 결과: 379800 KODEX S&P500이 추천 목록에서 완전 제외됨 (이전 22222222: score 40 exit_alert였음)
    - DB 상태 확인 + 로그 `etf_filtered_from_daily_cycle` 발생 확인
    - _Requirements: 3.3_

- [ ] 4. Phase 4: 주간 매크로 사이클 인프라
  - [ ] 4.1 scheduler에 weekly cron 추가
    - `trigger_weekly_macro` 함수 + CronTrigger(day_of_week='mon', hour=7, minute=0, timezone=TZ_KST)
    - 휴장일 시프트 로직 (다음 거래일까지 최대 5일)
    - 새 토픽 `stock.weekly_macro.requested` 발행
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ] 4.2 scheduler 단위 테스트
    - freezegun으로 월요일 07:00 / 월요일이 휴장(예: 어린이날) 시 화요일 트리거 검증
    - _Requirements: 8 (산하)_

  - [ ] 4.3 crewai main.py 라우팅 확장
    - `stock.weekly_macro.requested` 토픽 추가 컨슈머
    - 메시지 받으면 WeeklyMacroReportCrew kickoff
    - _Requirements: 4.1, 5.1_

  - [ ] 4.4 Tracking index 매핑 모듈 `crewai/crews/weekly_macro/etf_mapping.py`
    - 알려진 ETF (379800, 069500, 229200, 133690, 360200 등) → tracking_index
    - `tracking_index(ticker) -> Optional[str]`
    - _Requirements: 6.3_

- [ ] 5. Phase 5: 주간 매크로 Crew 구현
  - [ ] 5.1 `crewai/crews/weekly_macro/` 디렉토리 + base 파일 작성
    - __init__.py, crew.py, agents.py, tasks.py, tools.py
    - _Requirements: 5.1, 6.1_

  - [ ] 5.2 MacroWeeklyQueryTool 구현
    - week_start, week_end 입력 → 매크로 5지표 시작/종료값 + 변화율 반환
    - 데이터 없으면 null 명시
    - _Requirements: 5.1, 5.3_

  - [ ] 5.3 ETFHoldingsQueryTool 구현
    - `WHERE instrument_type IN ('index_etf', 'sector_etf')` + 사용자 매핑 포함
    - 반환: [{ticker, name, instrument_type, tracking_index, holder_chat_ids: [...]}]
    - _Requirements: 6.1_

  - [ ] 5.4 MacroSummarizerAgent + MacroSummaryTask 구현
    - 매크로 5지표 변화 → 한국어 3~5줄 요약 + tone 라벨
    - _Requirements: 5.2_

  - [ ] 5.5 ETFEvaluatorAgent + ETFEvaluationTask 구현
    - ETF별 verdict (favorable/caution/unfavorable) + 한국어 한 줄 사유
    - tracking_index 미매핑은 'caution' 기본 + 사유 명시
    - _Requirements: 6.1, 6.2, 6.3_

  - [ ] 5.6 WeeklyMacroReportCrew.on_complete()
    - LLM 출력 파싱 → weekly_macro_reports INSERT (idempotent on week_start)
    - per-user payload 구성 (chat_id → etf_evaluations)
    - Kafka publish: `stock.weekly_macro.report.completed`
    - _Requirements: 6.4, 7.4_

  - [ ] 5.7 단위 테스트 — Weekly crew
    - Mock LLM 결과로 on_complete 검증
    - per-user payload 분기 검증 (ETF 없는 사용자는 미포함)
    - _Requirements: 8.3, 9.3_

- [ ] 6. Phase 6: Notifier 주간 메시지 발송
  - [ ] 6.1 `stock.weekly_macro.report.completed` 토픽 컨슈머 추가
    - workers/telegram_notifier/main.py: 새 토픽 처리 핸들러
    - _Requirements: 7.1_

  - [ ] 6.2 메시지 포맷터 구현
    - design.md의 포맷 적용 (📅 / 📊 / 💬 / 📌 섹션)
    - 한국어
    - 빈 ETF 평가 시 skip
    - _Requirements: 7.1, 7.2_

  - [ ] 6.3 per-user fan-out 로직
    - per_user_etfs.keys() iterate
    - 각 chat_id에 메시지 발송
    - 실패 시 try/except, 다른 사용자에 영향 없음
    - _Requirements: 7.3_

  - [ ] 6.4 notifier 단위 테스트
    - 메시지 포맷 검증 (formatter 단독)
    - per-user fan-out 검증 (mocked telegram bot)
    - _Requirements: 8.3_

- [ ] 7. Phase 7: 통합 검증 + 배포
  - [ ] 7.1 dev 환경 통합 테스트
    - holdings에 ETF 등록 → daily 사이클이 ETF 제외하는지 검증
    - 주간 사이클 수동 트리거 → 메시지 발송까지 end-to-end
    - _Requirements: 모두_

  - [ ] 7.2 운영 마이그레이션 적용 (alembic upgrade head)
    - 기존 운영 holdings의 백필 결과 검증 (379800 → index_etf 등)
    - _Requirements: 9.2_

  - [ ] 7.3 배포 후 첫 월요일 검증
    - 07:00 자동 트리거 확인
    - 메시지 수신 확인
    - 로그 monitoring (`weekly_macro_started/_per_user_done/_completed`)
    - _Requirements: 10.3, 10.4_

  - [ ] 7.4 추가 ETF 발견 시 mapping 업데이트 (지속적)
    - `TICKER_TO_INDEX`에 추가
    - 또는 향후 DB 기반 매핑으로 마이그레이션 검토
    - _Requirements: 6.3_

## Notes

- Phase 1~3 완료 시점에 이미 사용자가 받는 잘못된 ETF exit_alert 알림은 멈춤 (가장 시급한 효과).
- Phase 4~6은 보강 알림으로 부가가치 — 시간 여유 있을 때 진행.
- weekly cycle은 LLM 호출 (요약 + ETF 평가 두 단계). 비용은 매주 1회로 미미.
- ETF 매핑은 점진 확장. 미매핑은 'caution' fallback이라 운영 중에도 안전.
- 백필 마이그레이션은 dev에서 검증 후 운영 적용 — multi_user_design 패턴(직접 ALTER + stamp) 활용 가능.
