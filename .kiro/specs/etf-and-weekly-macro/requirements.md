# Requirements Document: ETF Policy & Weekly Macro Report

## Introduction

본 문서는 stock-signal 시스템이 **지수형 자산(ETF/ETN 등)을 단일주와 동일한 평가축에서 평가하던 문제**를 해소하기 위한 정책 분리와 보강 알림을 정의한다.

**배경**: 2026-05-14 검증에서 보유한 KODEX S&P500 ETF(`379800`)가 KIS 외인/기관 상위 30위 시그널 풀에 들지 않아 `signals` row가 없고, 결과적으로 `exit_alert score=40`으로 매일 알림이 발송됐다. ETF는 본질적으로 추종 지수의 매크로 환경에 의해 움직이며, 단일주의 외인/기관 수급·RSI·볼린저 분석은 의미가 다르다.

**해결 방향**: (1) 일일 추천 풀에서 ETF 제외, (2) 주 1회 매크로 요약 + ETF 보유자별 우호도 판정 리포트로 별도 알림.

## Glossary

- **System**: stock-signal 전체 시스템
- **Holdings_Table**: PostgreSQL holdings 테이블 (사용자별 보유 종목)
- **Instrument_Type**: 종목의 자산 유형 (`single_stock` | `index_etf` | `sector_etf`)
- **Daily_Cycle**: 일일 추천 사이클 (intraday 16:30 + premarket 06:30)
- **Weekly_Macro_Report**: 매주 월요일 07:00 KST 발송되는 매크로 요약 + ETF 우호도 메시지
- **CrewAI**: stock_recommendation crew (signal/news/macro/synthesis)
- **Notifier**: worker-telegram-notifier (사용자별 fan-out)
- **Macro_5**: us10y / dxy / wti / sp500 / gold

## Requirements

### Requirement 1: holdings 테이블 instrument_type 컬럼 추가

**User Story:** As a system, I need to classify each holding as single_stock or ETF, so that the daily pipeline can route them through appropriate evaluation paths.

#### Acceptance Criteria

1. THE Holdings_Table SHALL include a column named `instrument_type` of type VARCHAR(20) NOT NULL DEFAULT `'single_stock'`.
2. THE allowed values SHALL be `'single_stock' | 'index_etf' | 'sector_etf'`.
3. THE System SHALL create an Alembic migration that adds the column with the default, then backfills existing rows via name-pattern detection (Requirement 2).
4. THE migration SHALL be reversible (downgrade removes the column).

### Requirement 2: 등록 시 자동 분류

**User Story:** As a system, I want to auto-detect ETF holdings at registration time from name patterns, so that users don't need to manually classify each ticker.

#### Acceptance Criteria

1. WHEN a new holding is inserted, THE System SHALL inspect the resolved `holdings.name` and set `instrument_type`:
   - Name contains any of: **KODEX, TIGER, KBSTAR, ARIRANG, KOSEF, HANARO, ETN, ACE, RISE** (case-insensitive) → `index_etf` if name also contains a market index keyword (S&P/KOSPI/KOSDAQ/NASDAQ/DOW/CSI/MSCI/지수) else `sector_etf`
   - 그 외 → `single_stock`
2. WHEN `holdings.name` is filled later via `_fill_holding_names` (KIS API), THE System SHALL re-evaluate `instrument_type` if it was `'single_stock'` AND the new name matches an ETF pattern.
3. THE pattern matcher SHALL be implemented as a pure Python function `infer_instrument_type(name: str) -> str` so it is unit-testable independently of DB.
4. THE detection rule SHALL be documented inline (function docstring + memory) so adding a new ETF brand is a single-place edit.
5. Manual override: backend admin endpoint or direct DB update SHALL allow correcting misclassified holdings (no special UI required for now).

### Requirement 3: 일일 추천 사이클에서 ETF 제외

**User Story:** As a user holding ETFs, I don't want to receive a daily exit_alert just because my ETF isn't in the KIS top-30 supply ranking.

#### Acceptance Criteria

1. WHEN CrewAI's SignalAnalyzer calls `holdings_query`, THE HoldingsQueryTool SHALL return only `instrument_type = 'single_stock'` rows. ETF tickers are not exposed to the daily evaluation pipeline.
2. WHEN the Notifier fan-outs daily recommendations to a user, IT SHALL filter out recommendations where the ticker matches any of that user's ETF holdings (defensive — in case CrewAI somehow picks one up).
3. The daily cycle SHALL not produce `exit_alert` rows for ETF tickers under any circumstances.
4. A new logging event `etf_filtered_from_daily_cycle` SHALL fire when an ETF is skipped, with ticker + instrument_type.

### Requirement 4: 주간 매크로 리포트 스케줄

**User Story:** As a system, I want to produce a weekly macro digest at a predictable cadence aligned with the start of the trading week.

#### Acceptance Criteria

1. A new cron entry SHALL fire **every Monday at 07:00 KST** to publish a `stock.weekly_macro.requested` Kafka event.
2. The scheduler SHALL use the existing `is_market_open` / holiday logic — if Monday is a Korean holiday, fire on the next trading day at 07:00 (i.e., Tuesday).
3. The event payload SHALL include: `job_id`, `target_date` (the Monday or holiday-adjusted day), `triggered_at`.
4. Manual trigger via Kafka publish SHALL be supported for testing (same payload shape).

### Requirement 5: 주간 매크로 리포트 — Macro 요약

**User Story:** As an ETF holder, I want a concise weekly summary of macro indicator movements so I understand the environment.

#### Acceptance Criteria

1. The report SHALL include the **5 macro indicators** with:
   - 이번 주 시작값 (지난 월요일 또는 가장 가까운 거래일 종가)
   - 이번 주 종료값 (오늘 = 보고 시점의 직전 거래일 종가)
   - 절대 변화 + 비율 변화 (%)
2. The report SHALL include an LLM-generated one-paragraph summary in Korean (3~5 lines) interpreting the week's macro tone (e.g., "달러 약세 + 미국 채권 금리 하락 → 위험자산 우호").
3. IF macro data is missing for the week's endpoints, THEN the report SHALL surface "데이터 부족" for that indicator (not silently drop).

### Requirement 6: 주간 매크로 리포트 — ETF 우호도 판정

**User Story:** As an ETF holder, I want each of my ETFs evaluated against the week's macro environment so I know whether to hold, watch, or reduce.

#### Acceptance Criteria

1. For each active user holding at least one ETF, THE Weekly_Macro_Report SHALL include a per-ETF evaluation:
   - Ticker + Name
   - 우호도 verdict: `favorable` | `caution` | `unfavorable`
   - 한 줄 사유 (한국어)
2. The verdict logic SHALL be rule-based with LLM-generated 사유:
   - **favorable**: 추종 지수와 같은 방향의 매크로 트렌드 (예: S&P500 ETF는 sp500 주간 상승 + 달러 약세)
   - **caution**: 혼조 신호 (예: 지수는 강하지만 달러 강세로 환차손 가능)
   - **unfavorable**: 반대 트렌드 명확 (예: 지수 하락 + 매크로 비우호)
3. Mapping of ticker → tracking index SHALL be data-driven (config table or hardcoded map for known ETFs). 미매핑 ETF는 "추종 지수 미확인 — 매크로 일반 우호도만 적용" fallback.
4. The report SHALL be skipped (no message sent) for users with **no ETF holdings**.

### Requirement 7: 메시지 포맷 및 전송

**User Story:** As an ETF holder, I want the weekly report to be readable on Telegram with consistent formatting.

#### Acceptance Criteria

1. THE message SHALL be sent via the existing telegram bot (worker-telegram-notifier 재사용 또는 weekly-specific 발송).
2. THE message format SHALL be:
   ```
   📅 주간 매크로 리포트 ({week_start} ~ {week_end})

   📊 매크로 5지표
   • 미 10년물: 4.50% → 4.42% (-0.08, -1.8%)
   • DXY:       105.2 → 104.6 (-0.6, -0.6%)
   • WTI:       …
   • S&P 500:   …
   • 금:        …

   💬 환경 요약
   <LLM 한국어 3~5줄>

   📌 내 ETF 평가
   🟢 KODEX 미국S&P500 (379800) — favorable
     → S&P500 주간 +2.1% + 달러 약세, 포지션 우호적

   🟡 KODEX 코스피 (069500) — caution
     → …

   ⚠️ 최종 판단은 본인이 직접
   ```
3. Send failure SHALL be logged but not block other users' delivery (per-user try/except).
4. The `stock.weekly_macro.report.completed` event SHALL include `recipient_count` for monitoring.

### Requirement 8: 단위 테스트

**User Story:** As a developer, I want each new component to be testable in isolation.

#### Acceptance Criteria

1. `infer_instrument_type` SHALL have property/unit tests covering: KODEX/TIGER/KBSTAR/ARIRANG/ETN brand detection, sector vs index sub-classification, plain stock name negative cases, empty/None input.
2. The daily-cycle ETF filter SHALL have a test verifying `HoldingsQueryTool` returns only `single_stock` rows.
3. The weekly macro report SHALL have an integration-level test (mocked LLM + mocked macro DB) verifying message format + per-ETF verdict mapping.
4. Migration SHALL be tested up/down with backfill verification (existing rows correctly classified).

### Requirement 9: Backward Compatibility

**User Story:** As an existing user, I want my current daily alerts to keep working with no regression.

#### Acceptance Criteria

1. Existing `single_stock` holdings SHALL continue receiving daily recommendations identically (single_stock is the default).
2. The migration backfill SHALL correctly identify existing ETFs (e.g., `379800 삼성 KODEX 미국S&P500`).
3. Users with **no ETF holdings** SHALL NOT receive any weekly_macro message (silent skip — see Requirement 6.4).
4. Removing a holding (`/del`) SHALL not require any instrument_type cleanup.

### Requirement 10: 로깅 및 관측성

**User Story:** As an operator, I want to monitor that the new pipeline behaves correctly.

#### Acceptance Criteria

1. `infer_instrument_type` decisions during `/start` and `/add` flows SHALL log `instrument_type_inferred` with ticker, name, type.
2. The daily ETF filter SHALL log `etf_filtered_from_daily_cycle` (Requirement 3.4).
3. The weekly worker SHALL log `weekly_macro_started` / `weekly_macro_per_user_done` / `weekly_macro_completed` with counts.
4. The scheduler SHALL log `weekly_macro_triggered` on every Monday cron fire (including holiday-shifted days).
