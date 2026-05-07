# CONTEXT.md — stock-signal

> 이 파일은 Claude의 프로젝트 기억 장치다.
> 모든 작업 시작 전 반드시 읽고, 작업 완료 후 반드시 업데이트한다.
> 마지막 업데이트: 2026-05-07 (KIS OAuth 토큰 공유 파일 캐시 — backend/crewai/data-collector가 docker named volume(`kis-token-cache`)으로 token.json 공유. 컨테이너 재시작/멀티 컨테이너 1분 재발급 차단 회피. data-collector 34→37.)

---

## 프로젝트 개요

- **프로젝트명**: stock-signal
- **목적**: AI 수급 기반 종목 추천 시스템 — 기관·외국인 연속 매수 신호와 보유 종목 탈출 타이밍을 AI가 뉴스 + 매크로 5지표와 종합 판단해 매일 텔레그램으로 5종목 이내 추천
- **현재 단계**: `초기설정` (Architect 산출물 완료, 구현 미시작)
- **듀얼 목적**: ① 실사용 도구 / ② Vibe Framework 카탈로그 표준 패턴 자체 검증 (Pilot)

### 핵심 문서
- 기획: `Vault/04_Projects/stock-signal/PRD.stock-signal.md`
- 기술 스펙: `Vault/04_Projects/stock-signal/SPEC.stock-signal.md`
- 테스트 사양: `TEST_SPEC.md` (본 프로젝트 루트)

---

## 스택 확정

| 모듈 | 채택 | 기술 | 버전 | 상태 |
|------|------|------|------|------|
| 백엔드 | ✅ | FastAPI | 0.110 | ⬜ 미시작 |
| AI 오케스트레이션 | ✅ | CrewAI (GPT-4o-mini) | latest | ⬜ 미시작 |
| Workers | ✅ | Python 3.11 | — | ⬜ 미시작 |
| Scheduler | ✅ | Python APScheduler | — | ⬜ 미시작 |
| DB | ✅ | PostgreSQL | 16 | ⬜ 미시작 |
| 메시지 큐 | ✅ | Kafka + Zookeeper | 7.6 | ⬜ 미시작 |
| 로그 수집 | ✅ | Promtail | 2.9 | ⬜ 미시작 |
| 로그 저장 | ✅ | Loki | 2.9 | ⬜ 미시작 |
| 로그 시각화 | ✅ | Grafana | 10.4 | ⬜ 미시작 |
| 인프라 | ✅ | Docker Compose | 3.9 | ⬜ 미시작 |
| 모바일 | ❌ | (Flutter — PRD 미포함) | — | — |
| 캐시 | ❌ | (Redis — 1일 1회 실행, 단일 사용자) | — | — |
| 파일 저장 | ❌ | (MinIO — 정규화 데이터는 PG로 충분) | — | — |
| 실시간 | ❌ | (Supabase — 모바일/realtime/인증 무용) | — | — |

상태 범례: ⬜ 미시작 / 🔄 진행중 / ✅ 완료 / ❌ 에러

---

## 모듈 구현 현황

### Backend (FastAPI)
- [x] 기본 앱 구조 (`backend/main.py`, `backend/Dockerfile`, `entrypoint.sh`)
- [x] PostgreSQL 연결 (`backend/core/database.py`, `pool_size=10`)
- [x] Alembic 환경 (`alembic.ini`, `migrations/env.py`) — `versions/20260430_0001_add_users_and_holdings_user_id.py` (multi-user 전환)
- [x] 라우터: `health`, `users`, `holdings`, `recommendations`, `jobs` (총 12개 엔드포인트 — `users` 4개, `PATCH /holdings/{ticker}` 1개 신규)
- [x] **multi-user 전환** (2026-04-30): users 테이블 + holdings.user_id FK. holdings 모든 작업이 chat_id로 active user 식별. /users/register, /users/{chat_id}/approve, /users/by-chat-id/{chat_id}, GET /users.
- [x] **/users/register 응답 코드 분리** (2026-05-03): 신규 생성 시 201, 이미 존재 시 200. listener가 admin 알림 트리거 판단에 사용.
- [x] **holdings.avg_price + PATCH 엔드포인트** (2026-05-03): NUMERIC(12,2) nullable 컬럼 추가. POST /holdings에 avg_price 옵션 인자, 신규 PATCH /holdings/{ticker}로 평단가 갱신/제거 (avg_price=null 명시 송신 시 clear). Alembic `20260503_0001_add_holdings_avg_price`.
- [x] 미들웨어: `add_response_headers` (X-Request-ID 에코, X-Response-Time)
- [x] 예외 핸들러: `VibeException`/`RequestValidationError`/`IntegrityError`/`HTTPException` → `ErrorResponse` 표준
- [x] structlog setup (`setup_logging("backend")`)
- [ ] **단독 기동 검증** (`docker compose up backend` → /health 200) — DevOps 재진입 단계

### CrewAI
- [x] **crewai 1.14.3 + crewai-tools 1.14.3 + pydantic ~2.11.9** (2026-04-30 업그레이드 — 0.30.11 + crewai-tools 0.2.6의 args_schema V1 validator가 pydantic V2 BaseModel을 서브클래스로 인식 못 하는 호환성 버그 해결)
- [x] `crewai/core/` Base 클래스 (BaseAgent / BaseTask / BaseCrew / VibeBaseTool) + logging / db (psycopg3 동기 풀) / kafka_io
- [x] `BaseTool` import: `from crewai.tools import BaseTool` (1.x 위치)
- [x] `crewai/crews/stock_recommendation/`
  - [x] tools.py — SignalQueryTool / NewsQueryTool / MacroQueryTool / HoldingsQueryTool (READ-only, JSON 출력)
  - [x] agents.py — 4 Agents (Signal/News/Macro/Synthesizer)
  - [x] tasks.py — 4 Tasks Sequential, Synthesizer가 앞 3개 결과를 context로 받음
  - [x] crew.py — StockRecommendationCrew(BaseCrew) + on_complete()에서 PG INSERT + JSON 파싱
- [x] `crewai/main.py` — Kafka consumer (stock.data.completed) → asyncio.to_thread(crew.kickoff) → publish completed/failed

### Workers
- [x] `worker-data-collector` — 한투 OpenAPI / yfinance / 네이버 스크래핑
  - [x] processor.py 분리: clients/{kis_api, yfinance_client, naver_scraper}.py
  - [x] consecutive_buy_days 계산 로직 (기관 OR 외국인 순매수 누적)
  - [x] **KIS API endpoint/TR_ID 운영 검증 완료** (2026-04-30):
    - `fetch_signals` → `/uapi/domestic-stock/v1/quotations/foreign-institution-total` (TR_ID `FHPTJ04400000`, HTS [0440])
    - `fetch_ticker_name` → `/uapi/domestic-stock/v1/quotations/search-stock-info` (TR_ID `CTPF1002R`)
    - TR_ID는 환경변수로 오버라이드 가능 (`KIS_TR_FOREIGN_INSTITUTION_TOTAL` / `KIS_TR_SEARCH_STOCK_INFO`)
    - 시장 범위는 `KIS_SIGNAL_MARKET_SCOPE` (0000:전체, 0001:코스피, 1001:코스닥)
    - 실키 OAuth 토큰 발급 smoke test 성공 (2026-04-30)
- [x] `worker-telegram-notifier` — Kafka consumer (stock.recommendation.completed) → PG 조회 → fan-out 송신
  - [x] formatter.py — PRD §13 알림 형식 + 추천 0개 시 "조건 충족 종목 없음"
  - [x] **multi-user fan-out** (2026-04-30): active users 전체 루프 + 사용자별 holdings 매칭. exit_alert는 사용자 보유 종목만 메시지에 포함, 한 사용자 송신 실패는 try/except로 격리해 다른 사용자에 영향 없음.
- [x] `worker-telegram-listener` — long-polling, 9개 명령어 (`/start /help /add /edit /remove /list /recent /reason /approve`)
  - [x] **multi-user 인증** (2026-04-30): backend `/users/by-chat-id`로 active 사용자만 명령어 처리. /start는 누구나 가능(register pending), /approve는 admin 전용. `TELEGRAM_AUTHORIZED_CHAT_ID` 단일값 의존 제거.
  - [x] **신규 등록 admin 알림** (2026-05-03): `/start` 응답이 201(신규) + status=pending이면 backend `/users` 조회로 active admin들에게 텔레그램 fan-out 알림. 메시지에 chat_id/username/`/approve` 명령 포함. 송신 실패는 try/except로 격리. ADMIN_BOOTSTRAP_IDS 본인은 자기 등록 시 알림 제외.
  - [x] **/add 평단가 옵션 + /edit 신규 명령** (2026-05-03): `/add 005930 [평단가]` 두 번째 인자 옵션, `/edit 005930 75000` 평단가 갱신, `/edit 005930 -` 평단가 제거. /list 출력에 평단가 표시. clear는 BackendClient.update_holding(clear_avg_price=True)로 avg_price=null 명시 송신.
  - [x] **종목명 수동 입력 지원** (2026-05-03): `/add 005930 코리안리`(2nd 비숫자 → name), `/add 005930 코리안리 75000`(3-arg = name+price), `/edit 005930 코리안리`(비숫자 → name 갱신). worker-data-collector는 `name IS NULL`인 row만 KIS API로 채우므로 사용자 수동 입력값은 보존.

### Scheduler
- [x] `scheduler/main.py` — APScheduler CronTrigger 2-cron (2026-05-01 분리, 2026-05-06 휴장일 갭 메타 추가)
  - **intraday (KST 16:30)**: `mode='intraday'`, signals 수집만 트리거
  - **premarket (KST 06:30)**: `mode='premarket'`, macro+뉴스+추천 트리거. `target_date`는 직전 거래일(signals 조회 기준), `target_trading_date`는 오늘(장 시작 예정일)
  - 두 시각 모두 `SCHEDULE_INTRADAY_HOUR/MINUTE` / `SCHEDULE_PREMARKET_HOUR/MINUTE` env로 오버라이드 가능
- [x] KRX 휴장일 캘린더 (`holidays.KR()` — 가벼움) + `previous_business_day()`
- [x] Kafka producer로 `stock.data.requested` 발행 (UUID job_id 생성, payload에 mode/target_trading_date 포함)

### DevOps
- [x] `docker-compose.yml` 베이스 (12개 서비스)
- [x] `docker-compose.dev.yml` (핫리로드, DEBUG 로그)
- [x] `docker-compose.staging.yml` (ghcr.io 이미지)
- [x] `docker-compose.prod.yml` (ghcr.io + restart:always + Grafana 외부 차단 + json-file 회전)
- [x] `.env.example` (GHCR_IMAGE_PREFIX, IMAGE_TAG 포함)
- [x] `infra/postgres/init.sql`, `infra/kafka/topics.yml`, `infra/loki/`, `infra/promtail/`, `infra/grafana/provisioning/`
- [x] `infra/grafana/dashboards/job-flow.json` (job_id 추적 + 에러/경고 카운트 + 서비스별 로그 발생량)
- [x] `.github/workflows/ci.yml` (PR 시 단위 테스트, Python 3.11, PYTHONPATH 7개 모듈 + OPENAI_MODEL_NAME)
- [x] `.github/workflows/deploy.yml` (test → 6 서비스 matrix 빌드 → ghcr.io push → SSH 배포)
- [x] 정합성 점검 (12 컨테이너 env 변수 / import 경로 / depends_on / healthcheck) — 4건 발견, 3건 즉시 수정
- [x] `RUNBOOK.md` (첫 부팅 / OCI 초기 셋업 / 헬스체크 / 롤백 / 운영 체크리스트)
- [ ] **전체 기동 검증** (사용자 환경에서 RUNBOOK §2 따라 수행)
- [ ] **/health 200 응답 검증** (사용자 검증 시점)
- [ ] **Grafana에서 job_id 추적 검증** (수동 Kafka publish 후 대시보드 확인)

> Stock-signal 미적용 (모듈 미선택): `infra/minio/` / `infra/redis/` / `infra/supabase/` / `infra/nginx/` (PRD 비기능 요구사항: 공개 inbound HTTP 없음)

---

## 컨테이너 목록 (12개)

```
postgres / zookeeper / kafka                       # 인프라 3
loki / promtail / grafana                          # 로그 3
backend / crewai / scheduler                       # 애플리케이션 3
worker-data-collector / worker-telegram-notifier / worker-telegram-listener   # 워커 3
```

> 메모리 추산 ~3GB → Oracle Cloud Free Tier ARM Ampere A1 (4 OCPU / 24GB RAM) 기준 충분.

---

## Kafka 토픽 목록

> 명명 규칙: `{서비스}.{리소스}.{이벤트}`. `infra/kafka/topics.yml` 참조.

| 토픽 | 발행자 | 구독자 | 페이로드 |
|------|-------|-------|---------|
| stock.data.requested | scheduler | worker-data-collector | `DataCollectionRequested` (mode='intraday'\|'premarket'). premarket은 추가로 `holiday_gap_days`(int) + `holidays_in_gap`(list[{date, reason}]) 포함 |
| stock.signals.completed | worker-data-collector | (모니터링) | intraday 완료 — signals만 수집 |
| stock.data.completed | worker-data-collector | crewai | premarket 완료 — macro+뉴스 수집 |
| stock.data.failed | worker-data-collector | DLQ handler | `DataCollectionFailed` |
| stock.recommendation.requested | (예약) | — | — |
| stock.recommendation.completed | crewai | worker-telegram-notifier | `RecommendationCompleted` |
| stock.recommendation.failed | crewai | DLQ handler | `RecommendationFailed` |
| stock.notify.requested | (예약) | — | — |
| stock.notify.completed | worker-telegram-notifier | (모니터링) | `NotifyCompleted` |
| stock.notify.failed | worker-telegram-notifier | DLQ handler | `NotifyFailed` |

---

## API 엔드포인트 현황

| Method | Path | 상태 | 응답 모델 | 설명 |
|--------|------|------|---------|------|
| GET | /health | ⬜ | dict | 헬스체크 |
| POST | /holdings | ⬜ | `HoldingResponse` | 보유 종목 추가 (avg_price 옵션) |
| GET | /holdings | ⬜ | `HoldingListResponse` | 보유 종목 목록 |
| PATCH | /holdings/{ticker} | ⬜ | `HoldingResponse` | 평단가 갱신/제거 |
| DELETE | /holdings/{ticker} | ⬜ | 204 | 보유 종목 제거 |
| GET | /recommendations | ⬜ | `RecommendationListResponse` | 특정 날짜 추천 (`?date=YYYY-MM-DD`) |
| GET | /recommendations/recent | ⬜ | `RecommendationListResponse` | 최근 N일 (`?limit=10`) |
| GET | /recommendations/by-ticker/{ticker} | ⬜ | `RecommendationItem` | 해당 종목 최근 1건 (`/reason` 백엔드) |
| GET | /jobs/{job_id} | ⬜ | `JobStatusResponse` | Job 상태 |

---

## 모듈 간 인터페이스

### 텔레그램 사용자 → Backend (간접, listener 경유)

```
사용자: /add 005930
  ↓
worker-telegram-listener → POST http://backend:8000/holdings { "ticker": "005930" }
  ↓
backend → INSERT INTO holdings ... → return HoldingResponse
  ↓
listener → 텔레그램 응답: "추가됨: 삼성전자(005930)"
```

### Scheduler → Worker → CrewAI → Notifier (Kafka 비동기 — 2-cron 분리)

```
[KST 16:30 (D일)]
scheduler  publish stock.data.requested(mode='intraday', target_date=D)
   ↓
worker-data-collector  consume → 한투 API → INSERT signals + holding name 채움
   ↓ publish stock.signals.completed(job_id, signals_count)   ← CrewAI 안 받음

[KST 06:30 (D+1)]
scheduler  publish stock.data.requested(mode='premarket', target_date=D, target_trading_date=D+1)
   ↓
worker-data-collector  consume → yfinance/네이버 → INSERT macro/news (news.date=target_trading_date)
   ↓ publish stock.data.completed(job_id, target_trading_date, counts)
crewai  consume → SignalAnalyzer(D) → NewsAnalyst(D+1) → MacroEnv → Synthesizer → INSERT recommendations
   ↓ publish stock.recommendation.completed(job_id, recommendation_count, ...)
worker-telegram-notifier  consume → SELECT recommendations → 텔레그램 fan-out 송신
   ↓ publish stock.notify.completed(job_id, message_id)
```

### Worker / CrewAI → PostgreSQL

- `users` (chat_id, status, is_admin) — 텔레그램 봇 multi-user 화이트리스트 (2026-04-30 추가)
- `holdings` (user_id FK, ticker, name, avg_price) — 사용자별 보유 종목 (UNIQUE(user_id, ticker)). avg_price는 NUMERIC(12,2) nullable.
- `signals` (date, ticker, agency_*, foreign_*, consecutive_buy_days) — 시장 공통
- `news` (date, ticker, title, url) — 시장 공통
- `macro_indicators` (date, us10y, dxy, wti, sp500, gold) — 시장 공통
- `recommendations` (date, target_trading_date, ticker, recommendation_type, score, reason_*) — 시장 공통, exit_alert 메시지 분기는 notifier가 사용자별 후처리
- `jobs` / `job_errors` (Vibe 표준) — 시장 작업 단위 추적, user 무관

---

## API Contract 현황

> Architect가 `API_CONTRACT_SKILL.md` 기반으로 설계.

### 공통 응답 헤더

| 헤더 | 설명 |
|------|------|
| X-Request-ID | 클라이언트 헤더 에코 또는 서버 자동 생성 (UUID v4) |
| X-Response-Time | 처리 시간 (ms 정수) |

### 에러 코드 목록 (stock-signal 사용분)

| error_code | HTTP | 사용 위치 |
|-----------|------|---------|
| INVALID_REQUEST | 400 | 필드 형식 오류 (ticker 형식 등) |
| MISSING_FIELD | 400 | 필수 필드 누락 |
| HOLDING_NOT_FOUND | 404 | 🆕 DELETE /holdings/{ticker} 미존재 |
| RECOMMENDATION_NOT_FOUND | 404 | 🆕 GET /recommendations/by-ticker/{ticker} 미존재 |
| JOB_NOT_FOUND | 404 | GET /jobs/{job_id} 미존재 |
| INTERNAL_ERROR | 500 | 서버 내부 오류 |
| SERVICE_UNAVAILABLE | 503 | 의존 서비스 (Kafka, PG, 한투 API) 불가 |

> 🆕 `HOLDING_NOT_FOUND`는 stock-signal에서 신규 도입. `API_CONTRACT_SKILL.md` 표준 목록에 추가 필요 (후속 Architect 작업).

### 공통 에러 응답 구조

```json
{
  "error_code": "HOLDING_NOT_FOUND",
  "message": "등록된 보유 종목이 아닙니다",
  "request_id": "req-uuid-...",
  "timestamp": "2026-04-25T15:35:00Z",
  "detail": {}
}
```

---

## 로그 이벤트 목록

> Grafana URL: http://localhost:3000 / 핵심 추적 쿼리: `{job="vibe-framework"} |= "<job_id>"`

| 서비스 | 이벤트명 | 레벨 | 설명 |
|--------|---------|------|------|
| scheduler | scheduler_triggered | INFO | 매일 트리거 발화 |
| scheduler | scheduler_skipped_holiday | INFO | KRX 휴장일 스킵 |
| scheduler | trigger_published | INFO | Kafka 발행 완료 |
| worker-data-collector | worker_received | INFO | Kafka 메시지 수신 |
| worker-data-collector | step_kis_api_start / _complete | INFO | 한투 API 호출 |
| worker-data-collector | step_yfinance_start / _complete | INFO | yfinance 호출 |
| worker-data-collector | step_naver_scrape_start / _complete | INFO | 네이버 스크래핑 |
| worker-data-collector | naver_scrape_blocked | WARNING | 차단 감지 → 종목 스킵 |
| worker-data-collector | worker_completed | INFO | 수집 완료 |
| worker-data-collector | worker_failed | ERROR | 수집 실패 |
| crewai | crew_started | INFO | Crew.kickoff 시작 |
| crewai | agent_step_start / _complete | INFO | Agent 각 단계 |
| crewai | recommendation_created | INFO | 추천 1건 생성 |
| crewai | crew_completed | INFO | 전체 완료 |
| crewai | crew_failed | ERROR | 전체 실패 |
| worker-telegram-notifier | telegram_send_start / _complete | INFO | 알림 송신 |
| worker-telegram-notifier | telegram_send_failed | ERROR | 송신 실패 |
| worker-telegram-listener | command_received | INFO | 사용자 명령어 수신 |
| worker-telegram-listener | command_unauthorized | WARNING | 인가되지 않은 chat_id |
| worker-telegram-listener | command_processed | INFO | 처리 완료 |
| worker-telegram-listener | admin_notified_new_registration | INFO | 신규 사용자 등록 알림을 admin에게 송신 완료 |
| worker-telegram-listener | admin_notify_failed | ERROR | admin 알림 송신 실패 (개별 admin 한정, 다른 admin은 계속) |
| worker-telegram-listener | admin_notify_skipped_no_active_admin | WARNING | active admin이 없어 알림 스킵 |
| worker-telegram-listener | admin_notify_skipped_list_failed | WARNING | backend `/users` 조회 실패로 알림 스킵 |
| backend | request_received | DEBUG | HTTP 요청 |
| backend | holding_added / _removed | INFO | 보유 종목 변경 |
| backend | request_error | ERROR | 검증 실패 |

---

## 테스트 완료 상태

> QA Engineer 실행 후 업데이트. 사양은 `TEST_SPEC.md` 참조.

| 테스트 유형 | 케이스 수 | 상태 | 통과/전체 | 마지막 실행 |
|-----------|---------|------|----------|-----------|
| 단위 — Backend (API) | 35 (24 holdings·jobs·recs·헤더 + 11 users·multi-user) | ✅ 전부 통과 | 35/35 (컨테이너) | 2026-05-01 |
| 단위 — Scheduler | 11 (4 is_market_open + 2 previous_business_day + 2 intraday + 3 premarket) | ✅ 전부 통과 | 11/11 (컨테이너) | 2026-05-01 |
| 단위 — Worker | 65 (34 data-collector intraday/premarket 분리 + 16 notifier fan-out·name fallback + 15 listener multi-user) | ✅ 전부 통과 | 65/65 (컨테이너 격리) | 2026-05-01 |
| 단위 — CrewAI | 18 (TEST_SPEC 13 ID, 4·3분할 포함) | ✅ 전부 통과 | 18/18 (컨테이너) | 2026-05-01 |
| **단위 합계** | **129** | ✅ | **129/129** | 2026-05-01 |
| 통합 | 9 (정상 5 + 장애 4) | ✅ 작성 완료 / 4건 수동시나리오로 skip 마킹 | (실행 미수행) | — |
| E2E | 5 | ✅ 작성 완료 / 2건(LLM·휴장일) skip 마킹 | (실행 미수행) | — |
| **전체 합계** | **143** | — | 단위 129/129 / 통합·E2E 작성 완료(skip reason 명확화) | — |

> **테스트 실행 방법**:
> - 호스트(Windows Python 3.13)에서 한 번에 돌리면 워커별 동일 이름 `core/` 패키지 sys.modules 캐시 충돌 + crewai 1.14 BaseTool import 위치 변경으로 부분 실패.
> - **정공법: 컨테이너 격리 실행**. `docker-compose.test.yml` + `scripts/run-unit-tests.sh`로 각 서비스 컨테이너 내부에서 자기 모듈 테스트만 실행.
> - 명령어: `bash scripts/run-unit-tests.sh` (전체) 또는 `bash scripts/run-unit-tests.sh backend crewai` (선택).
> - 통합/E2E는 Docker 풀 기동 + LLM/외부 API 환경 필요. 현재 작성만 완료(실행 미검증).

---

## 최근 에러 이력

| 날짜 | 모듈 | 에러 요약 | 해결 방법 | 상태 |
|------|------|---------|---------|-----|
| 2026-04-30 | crewai / workers | `event.get('job_id')` AttributeError on str — `producer.send_and_wait(TOPIC, json.dumps(...).encode())`가 producer의 `value_serializer`(이미 dumps+encode 수행)와 이중 인코딩 | (1) main.py에서 dict 직접 전달 (2) poison-message 핸들러 추가(non-dict 메시지는 commit + skip) — `crewai/main.py`, `workers/data_collector/main.py`, `workers/telegram_notifier/main.py` | ✅ 해결 |
| 2026-04-30 | tests/unit/backend | 일부 라우터에서 `BaseHTTPMiddleware`(`add_response_headers`)가 starlette/anyio TaskGroup과 FastAPI 예외 핸들러 충돌 → ExceptionGroup으로 감싸짐 | `core/dependencies.py` 순수 ASGI 미들웨어(`ResponseHeadersMiddleware`)로 변환 + `main.py` `add_middleware` 사용 | ✅ 해결 |
| 2026-04-30 | tests/unit/backend | DELETE/GET 등에서 SQLAlchemy `Task ... attached to a different loop` (pytest_asyncio function-scope 이벤트 루프 + 모듈 레벨 engine 풀 캐시) | `core/database.py`에 PYTEST_CURRENT_TEST 감지 시 `NullPool` 사용 분기 추가 | ✅ 해결 |
| 2026-04-30 | shared/schemas/recommendations.py | `RecommendationListResponse.date: Optional[date]` 필드명이 타입명을 가려 Pydantic 2가 type을 None으로 해석 → `none_required` 검증 오류 | `from datetime import date as date_type` alias로 import + 모든 타입 참조 변경 | ✅ 해결 |
| 2026-04-30 | crewai (운영) | Agent 빌드 시 `args_schema: subclass of BaseModel expected` ValidationError → 3회 retry 후 DLQ. crewai 0.30.11 + crewai-tools 0.2.6의 V1 `@validator(args_schema)` 데코레이터가 pydantic 2.7의 V2 BaseModel을 서브클래스로 인식 못 함. **단위 테스트는 `on_complete`/`Tool._run()` 직접 호출이라 Agent 빌드 단계 미커버 → 운영 검증 시점에 드러남** | crewai/crewai-tools 1.14.3 + pydantic ~2.11.9로 업그레이드 + `from crewai.tools import BaseTool`로 import 경로 변경 (1.x에서 `crewai_tools` 패키지 → `crewai.tools` 모듈로 이전) | ✅ 해결 |

---

## 다음 작업 시 주의사항

1. **PRD/SPEC은 Vault에 위치** (`Vault/04_Projects/stock-signal/`). 본 프로젝트 루트의 `CONTEXT.md` / `TEST_SPEC.md`와 분리.
2. **모듈 카탈로그 제외 사항** — Mobile/Redis/MinIO/Supabase는 명시적 미선택. 추후 도입 시 SPEC 갱신 후 진행.
3. **점수 가중치(50/25/25)와 컷오프(70/50)는 운영 검증 후 조정** 대상. 초안일 뿐 확정값 아님.
4. **휴장일 처리는 scheduler 한 곳에서만**. data-collector는 휴장일 체크 안 함 (트리거 자체가 안 옴).
5. **외부 API 인증은 환경변수만 사용**. 실제 키는 `.env.dev/.env.staging/.env.prod`에 (Git 커밋 금지).
6. **모든 비동기 작업은 Kafka 경유**. holdings CRUD 같은 단순 DB 쓰기는 FastAPI 직접 처리 (Vibe 원칙 적용).
7. **structlog + job_id 바인딩 필수**. Grafana 추적이 무용해짐.
8. **GHCR 주소 채울 시점**: GitHub 저장소 생성 직후 `.env.example`의 `GHCR_IMAGE_PREFIX=ghcr.io/<owner>/<repo>` 기본값을 갱신하고, `.env.staging` / `.env.prod`에 실제 값 설정. CI는 `${{ github.repository }}` 빌트인으로 자동 처리되므로 GitHub Actions 자체는 무관.
9. **GitHub Secrets 등록 필요 (배포 시점)**: `OCI_HOST`, `OCI_USER`, `OCI_SSH_KEY`. 단일 VM에 staging/prod를 디렉토리 분리(`/opt/vibe-staging/`, `/opt/vibe-prod/`)로 운영 가정.
10. **deploy.yml의 `deploy-staging` 단계는 Repository Variable `STAGING_ENABLED=true` 시에만 실행**. 단일 VM 운영 시 prod만 굴리려면 이 변수를 설정하지 않으면 됨.
11. **첫 배포 전 OCI VM 초기 셋업 필요**: Docker 설치, `/opt/vibe-prod/` 디렉토리 생성, `git clone`, `.env.prod` 배치, `docker login ghcr.io` 1회. 상세는 `skills/infra/DEPLOY_SKILL.md` "VM 초기 설정" 참조.
12. **`holdings.name`은 nullable + 다층 채움 정책 (2026-05-07 갱신)**: backend POST /holdings는 ticker 필수, name/avg_price는 옵션. 채움 우선순위:
    1. payload.name 명시 (사용자 직접 입력) → 그대로 사용
    2. payload.name 미지정 → **backend가 KIS API `fetch_ticker_name` 즉시 호출** (`backend/clients/kis_api.py`)
    3. KIS 호출 실패(키 미설정/네트워크/rt_cd 오류) → name=NULL, worker-data-collector가 다음 사이클에 `name IS NULL` row 보강
    
    이 다층 구조로 사용자는 `/add 005930` 직후 즉시 "✅ 추가됨: 삼성전자(005930)" 응답 받음 (worker 사이클 대기 불필요). worker 보강은 안전망. KIS_APP_KEY 미설정 환경(테스트 등)에서는 자동 fallback. 사용자가 `/edit 005930 코리안리`로 직접 입력한 name은 worker가 덮어쓰지 않음(name IS NULL 조건 유지).
13. **Backend는 Kafka 미발행**: 현 시점 backend의 모든 엔드포인트는 동기 PG 작업으로 끝나는 단순 CRUD/READ. 향후 비동기 트리거 추가 시 `core/kafka.py` 신설 + Producer 도입.
14. **TEST_SPEC API-002 (한투 API 자동 조회) 구현 위치 변경**: backend가 아닌 worker-data-collector에서 처리. QA Engineer는 본 변경을 반영해 worker 단위 테스트로 분류.
15. ~~**KIS API 호출 골격은 stub**~~ → **운영 검증 완료 (2026-04-30)**: `fetch_signals`는 외국인기관 매매가집계(`FHPTJ04400000`), `fetch_ticker_name`은 주식기본조회(`CTPF1002R`). 가집계 API 특성상 분리된 buy/sell 값은 없고 net만 채워짐(`signals.{agency,foreign}_{buy,sell}`는 NULL). TR_ID/시장범위는 환경변수로 오버라이드 가능. ⚠️ KIS는 **1분 내 동일 키 토큰 재발급 차단** 정책 — 컨테이너 재시작 직후 1분 이내 호출 시 403. 운영에서 토큰은 24h 유효하므로 컨테이너 살아있는 동안은 문제 없음. 잦은 재시작 환경이면 토큰 외부 캐시(Redis 등) 도입 검토.
16. ~~**네이버 뉴스 셀렉터(`a.tit, td.title a`)는 임시값**~~ → **운영 검증 완료 (2026-04-30)**: 셀렉터 `table.type5 td.title a.tit` + EUC-KR(cp949) 명시적 디코딩 적용. 단위 테스트 7건(`test_naver_scraper.py`)으로 fixture 기반 회귀 보호. 네이버 페이지 구조 변경 시 단위 테스트가 가장 먼저 깨질 것 → 운영 모니터링은 `step_naver_scrape_complete count=0` 알림으로 충분.
17. **Kafka 자동 토픽 생성 사용 중** (KAFKA_AUTO_CREATE_TOPICS_ENABLE=true): `infra/kafka/topics.yml`은 운영 가시성 문서일 뿐 명시 생성은 안 함. 첫 publish 시 자동 생성. 정확한 파티션 수가 필요하면 별도 init job 추가.
18. **worker-data-collector의 `consecutive_buy_days` 계산은 기관 OR 외국인 순매수 기준**: 둘 중 하나라도 순매수면 매수일로 카운트 (PRD "기관 또는 외국인 3일 이상 연속 순매수"에 부합).
19. **CrewAI Tool은 READ-only**: `recommendations` INSERT는 BaseCrew.on_complete()에서 (CREWAI_TOOL_SKILL §165 "Tool 안에서 DB 쓰기 금지" 원칙). Tool 추가 시 동일 원칙 적용.
20. **CrewAI는 동기 호출 + asyncio.to_thread**: Crew.kickoff()이 LLM 직렬 호출(분 단위)이므로 메인 이벤트 루프 차단을 피하기 위해 별도 스레드에서 실행. consumer commit 시점은 to_thread 완료 후.
21. **Synthesizer 출력 파싱은 견고하게**: LLM이 JSON을 마크다운 코드블록 안에 감쌀 수 있어 `crews/stock_recommendation/crew.py:_parse_recommendations`가 fallback 정규식으로 `[...]` 블록 추출. 파싱 실패 시 recommendations 0건 + warning 로그.
22. **점수 가중치(50/25/25) / 컷오프(70/50)는 SynthesizerAgent backstory에 가이드로 명시**: LLM 판단이라 정확히 적용된다는 보장 없음. 운영 1주 후 추천 결과의 score 분포를 보고 가이드 문구 보정.
23. **OPENAI_API_KEY 환경변수가 필수**: docker-compose.yml에서 crewai 서비스에 이미 주입. 키 미설정 시 crewai 컨테이너가 startup 직후 LLM 호출에서 실패. 운영 전 .env에 실키 채워야 함.
24. **CrewAI 모델 변수명은 `OPENAI_MODEL_NAME`** (NOT `OPENAI_MODEL`): CrewAI/LiteLLM이 자동 인식하는 표준 변수명. `.env.example` / `docker-compose.yml` 모두 갱신됨 (2026-04-29). 호출 후에도 gpt-4가 호출되면 AI Engineer가 BaseAgent에 `llm` 파라미터 명시 필요.
25. **CI/Deploy의 PYTHONPATH는 7개 모듈 디렉토리 콜론 구분**: `shared:backend:crewai:scheduler:workers/data_collector:workers/telegram_notifier:workers/telegram_listener`. 각 모듈에서 `from core.X` / `from schemas.X` import 가능하도록.
26. **backend healthcheck 추가됨**: Python 표준 라이브러리(urllib)로 `/health` 호출. start_period 30s, retries 5. telegram-listener가 `service_healthy`를 기다림. 첫 부팅 시 alembic upgrade 지연되면 start_period 연장.
27. **첫 부팅 / 운영은 RUNBOOK.md 따를 것**: `.env.dev` 작성, `docker compose up -d --build`, 텔레그램 명령어 테스트, 수동 Kafka publish로 전 흐름 추적 — 모두 RUNBOOK §2~5에 단계별 명시.
28. **Kafka 메시지 직렬화 일원화 원칙 (2026-04-30 도출)**: `core/kafka_io.py` `make_producer()`의 `value_serializer`가 dict→bytes 직렬화 책임을 단독으로 진다. 호출부(main.py)는 절대 `json.dumps(...).encode()`로 직접 인코딩하지 말고 dict 그대로 전달할 것. scheduler는 raw `AIOKafkaProducer`(serializer 없음) 사용 중이라 예외.
29. **Kafka 컨슈머 poison-message 패턴 (2026-04-30 도입)**: 모든 컨슈머는 `msg.value`가 dict 아니면 commit + skip + warning 로그(`poison_message_skipped`)를 남긴다. 이전 버전 메시지가 토픽에 남아있어도 무한 재시도 없이 지나가도록.
30. **단위 테스트 실행은 컨테이너별로**: 호스트 한 번에 돌리면 워커마다 동일 이름의 `core/` 패키지가 sys.modules 충돌함 + crewai 1.14의 `BaseTool` import 위치가 달라 호스트 호환성 깨짐. 정공법은 `bash scripts/run-unit-tests.sh` 또는 `docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.test.yml run --rm <service>-tests`. `docker-compose.test.yml`이 5개 test-runner 서비스 정의(backend / data-collector / telegram-notifier / telegram-listener / crewai).
31. **Backend 테스트 모드 NullPool 자동 적용**: `core/database.py`가 `PYTEST_CURRENT_TEST` 환경변수 감지 시 SQLAlchemy `NullPool` 사용. pytest_asyncio가 함수 스코프로 새 이벤트 루프를 만들 때 풀 캐시된 connection이 "different loop" 에러를 일으키는 이슈 회피. 운영 환경에는 영향 없음.
32. **공통 응답 헤더 미들웨어는 순수 ASGI**: `core/dependencies.py`의 `ResponseHeadersMiddleware`. 과거 `BaseHTTPMiddleware`(`add_response_headers` 함수형) 사용 시 starlette/anyio TaskGroup이 라우터 예외를 ExceptionGroup으로 감싸서 FastAPI 예외 핸들러가 못 받는 알려진 이슈가 있어 ASGI 미들웨어로 변환됨(2026-04-30). `add_response_headers` 이름은 하위 호환 alias로 유지.
33. **shared/schemas는 필드명-타입명 충돌 주의**: `RecommendationListResponse.date: Optional[date]`처럼 필드명이 타입명을 가리면 Pydantic 2가 타입을 None으로 해석해 `none_required` 검증 오류 발생. `from datetime import date as date_type` 패턴으로 alias하여 회피. 향후 스키마 추가 시 동일 충돌 검토.
34. **단위 테스트 미커버 영역 — Agent build/Crew kickoff 전체 흐름**: 본 프로젝트 crewai 단위 테스트는 `on_complete`(JSON 파싱 + DB INSERT) / `Tool._run()`(DB 조회) 직접 호출만 검증. 실제 `setup_agents()` → `Agent.__init__` → LLM 호출 흐름은 비용/시간 문제로 단위 테스트에서 제외. 따라서 Agent 빌드 단계의 호환성 버그(args_schema V1/V2 등)는 **반드시 운영 환경 수동 트리거로 검증**할 것. 의존성 업그레이드 시 단위 테스트 통과만으로 안전하다고 판단 금지.
35. **Vibe-net Docker 네트워크 명시 필요**: `docker-compose.test.yml` 같은 추가 override 파일에서 새 서비스 정의 시 `networks: [vibe-net]` 명시 + 파일 끝 `networks.vibe-net.external: true` 또는 `name: stock-signal_vibe-net` 매핑 필요. 안 하면 default 네트워크에 붙어서 `postgres`/`kafka` 호스트 resolve 실패.
36. **컨테이너별 이미지 태그 동기화 주의**: `docker-compose.dev.yml`은 `stock-signal-crewai:latest`(태그 미지정), `docker-compose.test.yml`은 `stock-signal-crewai:dev` 사용. requirements 갱신 시 둘 다 빌드 필요 (`docker compose build crewai` + `docker compose -f ... -f test.yml build crewai-tests`). 한 쪽만 빌드하면 회귀 검증 결과가 운영과 다를 수 있음.
37. **KIS OAuth 토큰 1분 내 재발급 차단 + 공유 파일 캐시 (2026-05-07 보강)**: 운영 중 24h 토큰을 메모리 캐시했지만 컨테이너 재시작/멀티 컨테이너 환경에서는 각자 새로 발급 시도해 1분 정책 충돌 → 403. **fix**: backend/crewai/worker-data-collector가 `kis-token-cache` named volume의 `/var/cache/kis/token.json`을 공유. `_ensure_token()` 흐름: 메모리 → 파일 → KIS 발급 → 파일 저장. 발급 실패(403) 시 파일 재조회 (다른 컨테이너가 막 발급했을 가능성). atomic write(tmp + rename)로 동시 쓰기 안전.
38. **Alembic stamp 운영 패턴 (2026-04-30)**: `entrypoint.sh`가 `alembic current` 빈 출력 시 `stamp head` 분기 처리하지만, dev 환경에서 직접 ALTER로 schema 변경한 경우 실제로 stamp가 안 되어 `upgrade head`가 다시 CREATE TABLE 시도하다 DuplicateTableError. 우회: `INSERT INTO alembic_version (version_num) VALUES ('<head_revision>')` 직접 실행. 운영(prod)에서는 첫 부팅 시 init.sql + stamp head 자동 처리되므로 문제 없음.
39. **Multi-user 화이트리스트 운영 정책 (2026-04-30)**: users 테이블 + chat_id 기반 인증. `/start`는 누구나 가능(pending 등록), 다른 명령어는 active만 처리. 첫 admin은 `STOCK_SIGNAL_BOOTSTRAP_ADMIN_CHAT_ID` 환경변수 또는 마이그레이션 시드. 운영 시작 전 admin chat_id 1개 미리 active+is_admin 처리 필요. 신규 사용자는 admin이 텔레그램 `/approve <chat_id>` 명령으로 승인.
40. **추천 데이터 시맨틱 (2026-04-30)**: signals/news/macro/recommendations/jobs는 모두 시장 공통 (user 분리 없음). exit_alert 메시지 분기만 사용자별 — notifier가 사용자 holdings와 매칭하여 보유 종목인 경우만 메시지에 포함. crewai는 모든 사용자 holdings 합집합을 후보 풀로 사용하므로 multi-user 환경에서도 LLM 호출은 1회/일.
41. **종목명 fallback 정책 (2026-05-01)**: notifier가 메시지 포맷 시 `recommendations.name`이 NULL이면 `holdings`에 등록된 동일 ticker의 name으로 보강. 우선순위: `recommendations.name` > `holdings.name`(distinct on ticker) > `ticker만 표시`. LLM 응답이 종목명을 누락해도 메시지가 읽기 좋게 유지됨. multi-user 환경에서 같은 ticker가 여러 사용자에 등록되어 있어도 어느 한 사용자의 name을 사용 (DISTINCT ON ORDER BY added_at DESC).
42. **2-cron 트리거 분리 (2026-05-01)**: scheduler 단일 KST 15:35 cron → 2-cron(`intraday` 16:30 + `premarket` 06:30 D+1)으로 분리. 사유: 미국 시장 마감(KST 05:00 서머타임)과 정리된 미국 매크로/뉴스 가져올 시간 확보. `stock.data.requested` 페이로드에 `mode`(`intraday`\|`premarket`) + `target_trading_date` 추가. data-collector main.py가 mode별 분기 처리 → intraday는 `stock.signals.completed`(CrewAI 안 받음), premarket은 기존 `stock.data.completed`(CrewAI 추천 트리거). signals 조회는 premarket의 `signal_date`(직전 한국 거래일) 기준, 뉴스 `news.date`는 `target_trading_date`로 저장.

44. **휴장일 갭 인식 (2026-05-06)**: scheduler `holidays_between(signal_date, target_trading_date)`로 두 거래일 사이 휴장일 메타 계산 → premarket payload에 `holiday_gap_days`(int) + `holidays_in_gap`(list[{date, reason}])로 동봉. data-collector가 stock.data.completed로 forwarding, crewai/main.py가 사람-친화 텍스트(`holiday_gap_text`)로 변환해 kickoff inputs에 주입 → SynthesisTask + NewsAnalysisTask description이 `{holiday_gap_text}`로 LLM에 전달. **NewsQueryTool은 단일 날짜 → date_from/date_to 범위 쿼리로 변경**. signal_date~target_trading_date 모든 뉴스(휴장일 갭 포함)를 한 번에 가져와 LLM이 시점 구분 가능. 추가 가이드: 직전 거래일에 부정 뉴스가 있었으나 그날 외+기관 강한 매수면 "흡수"로 해석 / 갭 동안 글로벌 호재는 다음 거래일 직접 반영. **사유**: 2026-05-06 삼성전자(005930) 케이스 — 5/4 부정 뉴스(씨티 목표주가 하향)만 LLM이 봤고 5/6 새벽 호재(반도체 랠리 등)는 못 봐서 잘못된 exit_alert 발생. `holidays.KR(language="ko")` 사용 (구 버전은 영어 fallback).

45. **yfinance 라이브러리 버전 노후 → 매크로 0건 수집 (2026-05-06 해결)**: yfinance 0.2.40이 Yahoo Finance API 변경(JSON 응답 형식)을 따라가지 못해 모든 심볼에서 `Expecting value: line 1 column 1` 에러 → macro_indicators 0 row. httpx로 직접 호출하면 200 OK이므로 네트워크/지역 차단은 무관. **`yfinance==1.3.0`으로 업그레이드해 5지표 모두 정상 수집**. 단위 테스트는 mock이라 영향 없음. 운영 모니터링: 매일 `yfinance_snapshot_collected` 로그의 `filled` dict가 전부 true인지 확인. 만약 또 깨지면 네이버 금융(`finance.naver.com/marketindex/`)으로 fallback 검토 — 단일 경로 유지가 단순하므로 1주 이상 정상이면 그대로 둠.

46. **CrewAI 데이터 흐름 3대 결함 fix (2026-05-06)**: 005930 exit_alert 잘못된 추천 검증 과정에서 발견.
    - **(a) SignalAnalyzer가 보유 종목을 후보 풀에서 누락**: `signal_query(min_consecutive≥3)`만 호출해 신규 후보만 도출 → 005930(consecutive=2) 같은 보유 종목이 NewsAnalyst/Synthesizer에 도달 전 사라짐. **fix**: SignalAnalyzerAgent에 `HoldingsQueryTool` 추가, SignalAnalysisTask에 "후보 풀 = (signal_query 결과) ∪ (holdings_query 결과)" 명시.
    - **(b) 보유 종목의 실제 수급 강도를 못 봄**: `min_consecutive≥3` 필터 때문에 보유 종목의 net_buy 데이터 자체가 안 잡힘 → LLM이 "consecutive < 3 = 약세"로 단정 → 모든 보유 종목 exit_alert. **fix**: `SignalQueryInput.tickers` Optional 필드 추가, Task에 "보유 종목별 `min_consecutive=0, tickers=[...]`로 한 번 더 호출해 net_buy 정량 평가" 가이드. 정량 가이드: net_buy 합 ≥ +30억 + consecutive ≥ 2 → '강한 매수 흡수형'.
    - **(c) MacroAnalysisTask 기준일 오류**: `near_date={target_date}`(=직전 한국 거래일)로 호출 → 다음 거래일에 가장 신선한 미국 매크로(글로벌 캘린더, 한국 휴장 무관)를 못 잡고 LLM이 -1일씩 17번+ 무한 시도. **fix**: `near_date={target_trading_date}`로 변경 + "글로벌 매크로는 한국 휴장과 무관, 1회 호출 후 available=false면 즉시 unknown 반환, 재시도 금지" 명시.
    - **검증**: 005930 동일 데이터로 3차 publish 결과 exit_alert=11 → **buy_hedge=75 (강한 매수 흡수형, 반도체 호재 인식)**. macro_query 호출도 17회+ → 1회로 정상화.

47. **분류 룰 LLM 일탈 → on_complete() 후처리 강제 (2026-05-06)**: 5차 검증에서 LLM이 73점에 exit_alert, 32점에 watch로 분류 룰 위반(score≥70은 buy_hedge, 보유+score<50은 exit_alert여야). **fix**: `crew.py on_complete()`가 INSERT 직전 score + holdings 멤버십으로 type 강제 재분류 (score≥70→buy_hedge, 50≤score<70→watch, score<50 AND 보유→exit_alert, score<50 AND 신규→제외). LLM 출력 type과 차이 시 `type_reclassified` 로그로 모니터링. SynthesisTask description도 동일 룰 명시 + "보유 H 5개 모두 출력 / 신규 N score 상위 3개". 6차 검증에서 5종목 모두 룰 일치 ✅.

48. **신규 후보 누락 — 운영 1주 모니터링 (2026-05-06)**: 5/6 검증 5차/6차에서 신규 매수 후보가 추천에 포함되지 않음. 5차는 SignalAnalyzer가 신규 후보 정확히 도출했지만 Synthesizer가 5종목 한도에서 누락, 6차는 SignalAnalyzer 자체가 JSON 깨고 영어 마크다운으로 응답해 new_candidates 정보 사라짐. **LLM 비결정성**으로 Task description 강제만으로는 한계. 임시 방침: **운영 1주 자연 누적 후 빈도 결정**. 자주 누락되면 후속 fix는 "후보 풀 결정을 코드로 deterministic하게 (crewai/main.py에서 holdings + signals 조회해 inputs에 명시 주입), LLM은 점수 산정만". 운영 모니터링: 매일 recommendations 테이블에서 보유 종목 외 ticker 비율 확인. 0% 지속이면 코드 fix 진행.

51. **`/reason` 명령 + GET /recommendations/by-ticker/{ticker} (2026-05-07)**: 사용자가 특정 종목의 최근 판단을 자세히 받아볼 수 있도록 신규 텔레그램 명령 + backend Detail 응답 endpoint 추가. **응답 구성** (`RecommendationDetailResponse`): (a) 가장 최근 recommendation 1건, (b) signals 최근 7일치 raw, (c) news rec.date~target_trading_date 최신 5건, (d) macro target_trading_date 이전 가장 최근 1건, (e) holding(chat_id 옵션 query, active 사용자 보유 시), (f) **외+기관 추정 평단가** = `SUM(net_buy_i × close_i) / SUM(net_buy_i)` — signals 양수 매수일 + KIS `inquire-daily-itemchartprice` 종가 가중. `backend/clients/kis_api.py.fetch_daily_prices()` 신설(TR_ID `FHKST03010100`). 데이터 부족(시그널 0건, KIS 응답 빈값 등) 시 평단가는 None으로 자연 생략. listener `_format_reason_message()`가 6개 섹션(헤더/수급/뉴스/매크로/추천 추정 매집가/내 보유)을 데이터 가용성에 따라 자동 표시. 추정값 한계는 메시지에 "추정값" 명시. **부수 fix**: `RecommendationItem.job_id`가 SQLAlchemy `UUID(as_uuid=True)`라 단일 객체 응답 시 pydantic `string_type` 검증 오류 → schema에 `field_validator(mode="before")`로 `uuid.UUID → str` 강제 변환.

50. **crewai on_complete()의 KIS name 보강 (2026-05-07)**: 5/7 알림에서 신규 후보 3개(018880/003530/006800)가 종목명 없이 ticker만 표시된 이슈. LLM(Synthesizer)이 JSON에 name을 NULL로 줘서 `recommendations.name=NULL` + notifier fallback이 holdings에만 의존(신규 후보는 holdings에 없음 → ticker 노출). **fix**: backend의 `clients/kis_api.py`와 같은 sync 패턴으로 `crewai/clients/kis_api.py` 신설 (httpx.Client + 모듈 단위 토큰 캐시). `crew.py on_complete()`가 INSERT 직전 LLM name 미명시 시 KIS 즉시 호출해 채움. 다층 폴백: LLM name > KIS API > NULL(notifier holdings 폴백 시도). crewai requirements는 httpx 명시 제거(crewai 1.14.3이 자동 0.28.x 설치, 0.27.0 핀과 충돌). 5/7 NULL 5건은 one-shot UPDATE로 즉시 보강(005930=삼성전자보통주, 003530=한화투자증권보통주, 006800=미래에셋증권보통주, 018880=한온시스템보통주, 000660=에스케이하이닉스보통주). 단위 테스트 +3 (KIS 채움 / LLM 우선 / 실패 NULL).

49. **5/7 운영에서 신규 후보 정상 추천 확인 + 메시지 포맷 두 섹션 분리 (2026-05-07)**: 5/7 06:30 KST 자동 트리거 결과 005930/000660 보유 평가 + **018880/003530/006800 신규 후보 3개가 watch로 정상 추천**됨 (#48에서 우려한 누락 미발생). 그러나 사용자 입장에서 watch 섹션에 보유와 신규가 섞여 보유 표시가 모호. 1차 fix(⭐ 마크)를 거쳐 최종 fix는 **메시지 큰 그림을 보유/신규 두 섹션으로 분리**: `📌 내 보유 종목 평가 (N종목)` + `🔍 신규 추천 (M종목)`. 분류(buy_hedge/watch/exit_alert)는 종목 줄에 이모지(🟢/🟡/🔴) + 라벨로 표시. ⭐ 마크와 범례는 제거. exit_alert는 보유 한정이라 자연스럽게 보유 섹션에만 등장. multi-user: 동일 ticker라도 사용자 A 보유면 A 메시지의 보유 섹션, B 미보유면 B 메시지의 신규 섹션. processor.py `_filter_for_user`가 사용자별 is_holding 마크 채워 새 RecItem 인스턴스로 반환(dataclass replace).
43. **postgres healthcheck dbname 명시 (2026-05-01)**: 기존 `pg_isready -U ${POSTGRES_USER}`는 dbname 미지정 → PG 기본 동작상 username과 같은 이름 DB(`stock`) 접속 시도 → 매 10초 `FATAL: database "stock" does not exist` 로그 오염. healthcheck 자체는 통과(exit 0)지만 진짜 에러가 묻힘. 수정: `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`. postgres 컨테이너 재생성 시 적용.

---

## 미결 사항 / 결정 필요

### 기획 영역 (PRD 미결 → 일부 잠정 결정, 운영 후 재검토)
- [x] 추천 0개인 날 알림 → **발송** ("오늘 조건 충족 종목 없음", PRD §18.1)
- [x] 공휴일/주말 처리 → **알림 자체 생략** (scheduler가 KRX 캘린더로 스킵)
- [ ] 휴장일에 "오늘 휴장입니다" 알림을 보낼지 (현재 완전 침묵) — 운영 1주 후 결정

### 기술 영역 (SPEC 미결)
- [ ] 점수 산식 가중치 (현재 50/25/25)와 컷오프 (70/50) — 운영 검증 후 조정
- [ ] `recommendations` 테이블에 "사후 실제 수익률" 컬럼 추가 여부 — 정확도 검증 목적
- [ ] `pandas_market_calendars` vs 자체 KRX 휴장일 캘린더 — Backend Engineer 결정
- [ ] LLM 응답 파싱 실패 시 retry 횟수 / 백오프 정책 — AI Engineer 결정

### 후속 Architect 작업
- [x] `API_CONTRACT_SKILL.md` 표준 에러 코드 목록에 `HOLDING_NOT_FOUND` 추가 (2026-04-26)
- [x] Vault의 `SPEC.stock-signal.md` §4 / §19 / §20 "11개" → "12개" 정정 (2026-04-26)

### DevOps 작업
- [x] 환경 오버라이드 (dev/staging/prod) (2026-04-26)
- [x] CI/CD 워크플로 (ci.yml / deploy.yml) (2026-04-26, 2026-04-29 PYTHONPATH/OPENAI_MODEL_NAME 보정)
- [x] Grafana job-flow 대시보드 (2026-04-26)
- [x] **DevOps 재진입 정합성 점검 + 수정** (2026-04-29)
  - OPENAI_MODEL → OPENAI_MODEL_NAME (CrewAI/LiteLLM 표준 변수명)
  - backend healthcheck 추가 (urllib 기반)
  - telegram-listener depends_on backend → service_healthy
  - ci.yml / deploy.yml에 PYTHONPATH 추가
- [x] `RUNBOOK.md` 작성 (첫 부팅 / OCI 셋업 / 헬스체크 / 롤백 / 운영 체크리스트)

### 사용자 환경 의존 검증 (RUNBOOK §2~3 따라 수동 수행)
- [x] `.env.dev` 작성 후 `docker compose up -d --build` → 12 컨테이너 healthy/Up (2026-04-29)
- [x] `curl http://localhost:8000/health` → 200 (2026-04-29)
- [x] CrewAI startup_complete (LLM 호출 검증은 별도) (2026-04-29)
- [ ] 텔레그램 봇 `/start` `/add` `/list` 정상 응답 (외부 키 채운 후)
- [x] 수동 Kafka publish → Grafana에서 동일 job_id로 전체 흐름 추적 가능 (2026-04-29)
- [ ] CrewAI 실제 LLM 호출 후 OpenAI usage 페이지에서 `gpt-4o-mini` 호출 확인 (gpt-4 호출되면 AI Engineer 후속 작업)

### 첫 부팅 시 보정한 추가 이슈 (2026-04-29)
- [x] `infra/loki/loki-config.yml` — WAL 권한 에러 → minimal config (common.path_prefix + tsdb v13)로 교체
- [x] `crewai/core/base_tool.py` — `crewai.tools.BaseTool` → `crewai_tools.BaseTool` (CrewAI 0.30+ 호환)
- [x] `crewai/requirements.txt` — `psycopg-pool` 패키지 추가 (psycopg와 별도 install 필요)

### Kafka 이중 인코딩 + 단위테스트 작성 (2026-04-30)
- [x] **Kafka 이중 인코딩 버그**: 3개 서비스(crewai / data_collector / telegram_notifier)가 `producer.send_and_wait(TOPIC, json.dumps(payload).encode())`로 호출하는데, `make_producer()`의 `value_serializer`도 같은 처리를 수행 → 메시지가 `"b'{...}'"` 형태 문자열로 이중 인코딩됨. 컨슈머가 `event.get(...)` 호출 시 AttributeError. **해결**: main.py에서 dict 직접 전달 + poison-message 핸들러로 옛 메시지 정상 스킵
- [x] **단위 테스트 작성**: tests/unit/workers/ (32 케이스 — data_collector 11 + telegram_notifier 10 + telegram_listener 11), tests/unit/crewai/ (18 테스트 = TEST_SPEC 13 ID, CREW-E001/CREW-007 분할 포함). 외부 의존성(KIS/yfinance/네이버/Telegram Bot/LLM)은 Fake/Mock으로 대체
- [x] **컨테이너별 격리 실행 인프라**: `docker-compose.test.yml` (5 test-runner 서비스 — 각 서비스 이미지 + tests/ 마운트 + pytest 런타임 설치) + `scripts/run-unit-tests.sh` (선택적 서비스 지정 가능). `tests/conftest.py`는 `POSTGRES_HOST` 환경변수로 호스트/컨테이너 모두 동작
- [x] **단위 테스트 74/74 통과 달성** (2026-04-30): backend 24, data-collector 11, telegram-notifier 10, telegram-listener 11, crewai 18

### 통합/E2E 테스트 작성 (2026-04-30)
- [x] tests/integration/ — INT-001~005, INT-E001~004 (Docker compose 풀 기동 + Kafka producer/consumer 직접 사용. 외부 stop/start, 휴장일은 수동 시나리오로 skip 마킹)
- [x] tests/e2e/ — E2E-001~005 (HTTP + Kafka 혼합. LLM·실 텔레그램·휴장일 시뮬은 skip 마킹)
- [x] `tests/integration/conftest.py` — Docker 미기동 시 session-level skip + `dev_pool` (asyncpg) + `kafka_producer/consumer_factory` + `wait_for` 폴링 헬퍼
- [ ] 통합/E2E 실제 실행 검증은 LLM 키 + 외부 API mock 인프라 도입 후

### 전체 파이프라인 운영 검증 + KIS API 보정 (2026-04-30)
- [x] **KIS API stub 제거**: `fetch_signals`(외국인기관 매매가집계 `FHPTJ04400000`) + `fetch_ticker_name`(주식기본조회 `CTPF1002R`) 실 endpoint/TR_ID 적용. 환경변수로 오버라이드 가능. 단위 테스트 8건 추가 (data-collector 19/19)
- [x] **실키 OAuth 토큰 발급 smoke test 성공** (2026-04-30): KIS 토큰 발급 정상 (1분 내 재발급 차단 정책 확인)
- [x] **수동 Kafka publish → 전체 5단계 파이프라인 정상 흐름 검증** (2026-04-30):
  1. `stock.data.requested` 발행 → data-collector consume → KIS API 토큰 발급 → kis_signals_collected (장 개시 전이라 0건) → yfinance/네이버 → `stock.data.completed` 발행
  2. crewai consume → 4 Agent 빌드 → LLM 4회 호출 (45초) → 추천 0건 INSERT → `stock.recommendation.completed`
  3. telegram-notifier consume → DB 조회 → 실 텔레그램 송신 (message_id 145, 146) → `stock.notify.completed`
  4. 모든 단계에서 동일 job_id 보존, structlog JSON 정상, DLQ 로직 검증됨
- [x] **crewai 1.14.3 업그레이드** (2026-04-30): args_schema V1/V2 호환성 버그 해결. 단위 18/18 회귀 통과 + 운영 흐름 정상

### 후속 운영 안정화 (2026-04-30)
- [x] **B. 네이버 뉴스 스크래퍼 운영 검증** (2026-04-30): 실 페이지 캡처 분석으로 셀렉터 정확화 (`a.tit, td.title a` → `table.type5 td.title a.tit`). EUC-KR(cp949) 명시적 디코딩 추가 (Content-Type charset 누락 페이지 대비). 단위 7건 추가 (data-collector 26/26).
- [x] **C-1. 휴장일 처리 단위 테스트** (2026-04-30): scheduler `is_market_open` + `trigger_daily` 휴장일 스킵을 freezegun으로 검증. 6/6 통과. 통합 INT-E004 / E2E-004는 동등 검증된 것으로 reason 갱신하여 skip 유지.
- [x] **C-2. 외부 의존성 stop/start 시나리오** (2026-04-30): INT-E001/E002는 aiokafka/SQLAlchemy 라이브러리 자동 재연결 보장 영역으로 판단. 통합 테스트 자동화 ROI 낮음 → reason 명확화 후 skip 유지. 운영 모니터링(Grafana 알림)으로 대체.
- [x] **C-3. mock LLM 인프라 도입처** (2026-04-30): E2E-003 결정적 검증을 위한 mock LLM 인프라(respx OpenAI 모킹 또는 monkeypatch) 도입처를 reason에 명시. 추정 1~2시간 작업으로 별도 작업 단위 분리. skip 유지.

### Multi-user 전환 (2026-04-30)
**기획 결정**: 1A(소규모 ≤10명 화이트리스트 + admin 승인) + 2A(시장 공통 추천 + 사용자별 exit_alert 후처리) + 3A(notifier fan-out 단순).

- [x] **데이터 모델**: `users` 테이블 신설 (chat_id, status, is_admin, approved_by/at). `holdings.user_id NOT NULL FK`. `recommendations / signals / news / macro_indicators / jobs`는 시장 공통 (user 분리 없음).
- [x] **Alembic version `20260430_0001`**: 운영 마이그레이션용. `STOCK_SIGNAL_BOOTSTRAP_ADMIN_CHAT_ID` 환경변수 기반 admin 시드 + 기존 holdings 백필. dev DB는 직접 ALTER + `INSERT INTO alembic_version VALUES ('20260430_0001')` 스탬프로 적용.
- [x] **Backend**: `/users/register`, `/users/{chat_id}/approve`, `/users/by-chat-id/{chat_id}`, `GET /users` 신규. holdings 모든 작업이 `chat_id` 파라미터로 active user 식별 → user_id 기반 처리.
- [x] **Listener**: `/start` 시 `/users/register` 호출(신규는 pending). 모든 명령어는 `/users/by-chat-id`로 active 사용자만 처리(pending/inactive/미등록은 안내만). `/approve <chat_id>` admin 전용 명령 추가. 기존 `TELEGRAM_AUTHORIZED_CHAT_ID` 단일값 의존 제거.
- [x] **Notifier fan-out**: `notify(pool, bot, target_trading_date)` — chat_id 파라미터 제거. 내부에서 `SELECT users WHERE status='active'` → 각 사용자 holdings로 exit_alert 필터링 후 송신. 한 사용자 송신 실패는 `try/except (TelegramError)`로 격리, RetryAfter는 raise해서 main.py가 sleep+재처리.
- [x] **CrewAI**: 코드 동작 무변경. `HoldingsQueryTool` description + `SynthesizerAgent.backstory`만 갱신해 "전체 보유 합집합 = 시장 공통 후보 풀, 사용자별 분기는 notifier 후처리" 시맨틱 명시.
- [x] **운영 검증** (2026-04-30): backend 재시작 후 multi-user 흐름 end-to-end 통과. (1) admin이 holdings 추가, (2) pending user 등록, (3) admin이 승인, (4) Kafka publish → notifier가 active 2명에게 fan-out 송신 (message_id 158/159), inactive 자동 제외, 사용자별 exit_alert 분기 정상.
- [x] **단위 테스트 회귀**: 95/95 → **118/118**. 신규 23건 (backend users 8 + multi-user holdings 3, listener multi-user 4, notifier fan-out 3 + exit_alert filter 2 + isolation 1, scheduler/data-collector 그대로).

### AI Engineer 후속 작업 (조건부)
- [ ] BaseAgent에 `llm=ChatOpenAI(model=...)` 파라미터 명시 — 위 항목에서 gpt-4 호출이 확인될 경우만
- [x] CrewAI 0.30+ 호환성 — `BaseTool` import 경로 변경: `crewai.tools` → `crewai_tools` (DevOps 재진입 시 보정, 2026-04-29)
- [ ] 추가 import 경로 이슈 발생 시 점검: CrewAI 버전 업그레이드 시 `agents.py`/`tasks.py`/`crew.py`의 다른 import도 호환성 검증 필요

---

## 다음 작업

| 단계 | 페르소나 | 전달 파일 | 시작 조건 | 주의사항 |
|------|--------|---------|---------|---------|
| 1 | ~~DevOps Engineer~~ (선행 부분 완료) | docker-compose 4종 + ci/deploy.yml + Grafana 대시보드 | — | 전체 기동 검증은 5번 (DevOps 재진입) |
| 2 | ~~Backend Engineer~~ ✅ 완료 (2026-04-26) | `backend/` 전체 (25 files) | — | holdings.name은 nullable → worker 첫 사이클에서 채움. Kafka producer 미포함 (현 시점 발행 없음) |
| 3 | ~~Worker Engineer~~ ✅ 완료 (2026-04-26) | `workers/` 3종 + `scheduler/` (28 files) | — | KIS API endpoint/TR_ID는 운영 검증 시 보정 필요 (현재 stub) |
| 4 | ~~AI Engineer~~ ✅ 완료 (2026-04-26) | `crewai/` 전체 (17 files) | — | Tool은 READ-only / INSERT는 BaseCrew.on_complete()에서. 실제 GPT 호출 검증은 통합 단계 |
| 5 | ~~DevOps Engineer (재진입)~~ ✅ 완료 (2026-04-29) | 위 모든 산출물 | AI 완료 시 | `docker compose up -d` 전체 기동 → /health → Grafana 추적 검증 |
| 6 | ~~QA Engineer (단위테스트)~~ ✅ 완료 (2026-04-30) | TEST_SPEC.md 단위 74 케이스 + Kafka 버그 fix + Backend 미들웨어/스키마 fix | 5번 완료 후 | 단위 74/74 통과(컨테이너 격리). `scripts/run-unit-tests.sh`로 회귀 검증 |
| 7 | ~~QA Engineer (통합·E2E 작성)~~ ✅ 완료 (2026-04-30) | tests/integration/ 9 + tests/e2e/ 5 = 14 케이스 작성 | 6번 완료 후 | 작성 완료. 실제 실행 검증은 외부 API mock + LLM 키 환경 도입 후 |
| 8 | ~~**운영 검증 핵심 흐름**~~ ✅ 완료(2026-04-30) | LLM 키/외부 API 실키로 전체 파이프라인 1회 수동 트리거 | 7번 완료 후 | ~~KIS API endpoint/TR_ID 보정~~ + ~~crewai 1.14.3 업그레이드(args_schema 버그)~~ + ~~5단계 흐름 검증(텔레그램 송신까지)~~ |
| 9 | ~~**운영 안정화 핵심**~~ ✅ 완료(2026-04-30) | B 네이버 셀렉터 + C-1 휴장일 단위 테스트 + C-2/C-3 reason 명확화 | 8번 완료 후 | 단위 95/95 통과 |
| 10 | ~~**multi-user 전환**~~ ✅ 완료(2026-04-30) | users 테이블 + 화이트리스트 + listener 인증 + notifier fan-out (1A+2A+3A) | 9번 완료 후 | 단위 118/118 + 운영 fan-out 검증 |
| 11 | **D. 운영 1주 후 점수/컷오프 재평가** | 운영 데이터 누적 후 가중치(50/25/25)·컷오프(70/50) 검토 | 운영 ≥ 7일 데이터 | recommendations 테이블의 score 분포 확인. SynthesizerAgent backstory 가이드 문구 보정. |
| 12 | **(옵션) mock LLM 인프라 도입** | E2E-003 활성화 위한 respx OpenAI 모킹 또는 crewai.LLM monkey-patch | 별도 결정 | 추정 1~2시간. 결정적 E2E 검증 가치 vs 인프라 유지비 트레이드오프 |

---

## 환경별 실행 명령어

```bash
# 개발 (로컬, 핫리로드)
docker compose -f docker-compose.yml -f docker-compose.dev.yml --env-file .env.dev up -d

# 스테이징 (OCI VM, ghcr.io 이미지)
docker compose -f docker-compose.yml -f docker-compose.staging.yml --env-file .env.staging up -d

# 운영 (OCI VM)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d

# 헬스체크
curl -fsS http://localhost:8000/health

# Grafana (개발/스테이징만 외부 접근, 운영은 SSH 터널)
open http://localhost:3000   # 비밀번호: GRAFANA_ADMIN_PASSWORD

# Kafka 토픽 확인
docker compose exec kafka kafka-topics --bootstrap-server kafka:9092 --list

# PostgreSQL 접속
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB
```

## 서비스 포트 (개발 기준)

```
Backend (FastAPI)  : 8000
PostgreSQL         : 5432
Kafka              : 9092
Loki               : 3100
Grafana            : 3000   ← Job 흐름 추적 메인 UI
```

> 운영(prod)에서는 Grafana 외부 포트 차단 — SSH 터널로만 접근.
