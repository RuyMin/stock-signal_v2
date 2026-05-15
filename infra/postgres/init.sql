-- stock-signal PostgreSQL 초기 스키마
-- 단일 진실의 원천 (Single Source of Truth) — Alembic은 이후 변경분만 관리

-- ─── 공통: jobs / job_errors (Vibe 표준) ─────────────────

CREATE TABLE IF NOT EXISTS jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type    VARCHAR(100) NOT NULL,
    status      VARCHAR(20)  NOT NULL DEFAULT 'queued',
    progress    INTEGER      NOT NULL DEFAULT 0,
    result      JSONB,
    error_msg   TEXT,
    metadata    JSONB,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS job_errors (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID REFERENCES jobs(id) ON DELETE CASCADE,
    service     VARCHAR(50),
    error_code  VARCHAR(100),
    message     TEXT,
    traceback   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_errors_job_id ON job_errors(job_id);

-- ─── 도메인: 사용자 (multi-user, 소규모 화이트리스트) ──────

CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id             BIGINT UNIQUE NOT NULL,                -- 텔레그램 chat_id
    telegram_username   VARCHAR(64),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending | active | inactive
    is_admin            BOOLEAN     NOT NULL DEFAULT FALSE,
    approved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at         TIMESTAMPTZ,
    registered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status) WHERE status = 'active';

-- ─── 도메인: 보유 종목 (사용자별) ───────────────────────

CREATE TABLE IF NOT EXISTS holdings (
    id              SERIAL PRIMARY KEY,
    user_id         UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker          VARCHAR(10)  NOT NULL,
    name            VARCHAR(100),
    avg_price       NUMERIC(12,2),
    added_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- etf-and-weekly-macro spec: 'single_stock' | 'index_etf' | 'sector_etf'
    instrument_type VARCHAR(20)  NOT NULL DEFAULT 'single_stock',
    UNIQUE(user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_holdings_user_id ON holdings(user_id);
CREATE INDEX IF NOT EXISTS idx_holdings_instrument_type
    ON holdings(instrument_type)
    WHERE instrument_type != 'single_stock';

-- ─── 도메인: 수급 시계열 ───────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
    id                      BIGSERIAL PRIMARY KEY,
    date                    DATE         NOT NULL,
    ticker                  VARCHAR(10)  NOT NULL,
    agency_buy              BIGINT,
    agency_sell             BIGINT,
    agency_net_buy          BIGINT,
    foreign_buy             BIGINT,
    foreign_sell            BIGINT,
    foreign_net_buy         BIGINT,
    consecutive_buy_days    INTEGER      NOT NULL DEFAULT 0,
    collected_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- momentum/technical indicators (Alembic 20260108_0001 — momentum-signals spec)
    one_day_net_buy         BIGINT,
    three_day_avg_net_buy   BIGINT,
    volume_ratio            NUMERIC(6,2),
    rsi                     NUMERIC(5,2),
    ma_alignment            VARCHAR(20),
    bollinger_position      NUMERIC(6,3),
    trading_value           BIGINT,
    UNIQUE(date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_signals_date_ticker ON signals(date, ticker);
CREATE INDEX IF NOT EXISTS idx_signals_consecutive
    ON signals(date, consecutive_buy_days)
    WHERE consecutive_buy_days >= 3;

-- ─── 도메인: 종목별 뉴스 ───────────────────────────────

CREATE TABLE IF NOT EXISTS news (
    id              BIGSERIAL PRIMARY KEY,
    date            DATE         NOT NULL,
    ticker          VARCHAR(10)  NOT NULL,
    title           TEXT         NOT NULL,
    url             TEXT,
    source          VARCHAR(50)  NOT NULL DEFAULT 'naver',
    collected_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_date_ticker ON news(date, ticker);

-- ─── 도메인: 매크로 5지표 ──────────────────────────────

CREATE TABLE IF NOT EXISTS macro_indicators (
    id              SERIAL PRIMARY KEY,
    date            DATE UNIQUE NOT NULL,        -- 미국 장 종가 기준일 (시차 처리)
    us10y           NUMERIC(6,3),                -- 미국 국채 10년 (%)
    dxy             NUMERIC(8,3),                -- 달러 인덱스
    wti             NUMERIC(8,2),                -- WTI 유가 (USD)
    sp500           NUMERIC(10,2),               -- S&P500
    gold            NUMERIC(8,2),                -- 국제 금 (USD/oz)
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── 도메인: AI 추천 결과 ──────────────────────────────

CREATE TABLE IF NOT EXISTS recommendations (
    id                      BIGSERIAL PRIMARY KEY,
    date                    DATE         NOT NULL,         -- 추천 발행일 (장 마감일)
    target_trading_date     DATE         NOT NULL,         -- 추천 대상 다음 거래일
    ticker                  VARCHAR(10)  NOT NULL,
    name                    VARCHAR(100),
    recommendation_type     VARCHAR(20)  NOT NULL,         -- buy_hedge | watch | exit_alert
    score                   INTEGER      NOT NULL CHECK (score BETWEEN 0 AND 100),
    reason_supply           TEXT,
    reason_news             TEXT,
    reason_macro            TEXT,
    estimated_avg_price     NUMERIC(12,2),                 -- buy_hedge에서만
    job_id                  UUID REFERENCES jobs(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_date ON recommendations(date DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_ticker ON recommendations(ticker);
CREATE INDEX IF NOT EXISTS idx_recommendations_type ON recommendations(date, recommendation_type);

-- ─── 도메인: 주간 매크로 리포트 (etf-and-weekly-macro spec) ────

CREATE TABLE IF NOT EXISTS weekly_macro_reports (
    id              BIGSERIAL PRIMARY KEY,
    week_start      DATE         NOT NULL,
    week_end        DATE         NOT NULL,
    job_id          UUID,
    macro_summary   TEXT,
    macro_values    JSONB,
    etf_evaluations JSONB,
    generated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(week_start)
);
