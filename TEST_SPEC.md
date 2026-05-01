# TEST_SPEC.md — stock-signal

> Architect 작성 테스트사양서. QA Engineer는 본 사양서 기반으로만 테스트 코드를 작성한다.
> 임의 케이스 추가 금지. 누락 발견 시 본 SPEC을 먼저 갱신.
> 마지막 업데이트: 2026-04-25

---

## 테스트 범위 요약

| 계층 | 대상 | 테스트 유형 | 정상 / 에러 |
|------|------|-----------|-----------|
| API | Backend FastAPI 7개 엔드포인트 | 단위 | 12 / 11 + H 3 |
| Worker | 3개 Worker (data-collector / telegram-notifier / telegram-listener) | 단위 | 18 / 13 |
| CrewAI | StockRecommendationCrew (4 Agent + 4 Task) | 단위 (mock LLM) | 9 / 4 |
| 통합 | Kafka 파이프라인, 모듈 간 연동 | 통합 (Docker 기동) | 5 / 4 |
| E2E | 사용자 시나리오 | E2E (Docker 기동) | 5 |

> "에러 케이스가 정상 케이스의 2배 이상" 원칙은 외부 API 장애/스크래핑 차단/LLM 실패 등이 stock-signal의 주된 리스크라는 점을 반영.

---

## 1. API 엔드포인트 테스트 사양

> 에러 응답 구조는 `shared/schemas/common.ErrorResponse` 준수.

### 1.1 POST /holdings — 보유 종목 추가

**정상 케이스:**

| ID | 시나리오 | 입력 | 기대 응답 | 상태 |
|----|---------|------|---------|-----|
| API-001 | 유효한 종목코드 추가 | `{"ticker": "005930"}` | `HoldingResponse` (id, ticker, name, added_at) | 201 |
| API-002 | 한투 API에서 종목명 자동 조회 | `{"ticker": "005930"}` | name = "삼성전자" | 201 |

**에러 케이스:**

| ID | 시나리오 | 입력 | 기대 error_code | 상태 |
|----|---------|------|-----------|-----|
| API-E001 | ticker 누락 | `{}` | `MISSING_FIELD` | 400 |
| API-E002 | ticker 형식 오류 (영문) | `{"ticker": "ABC123"}` | `INVALID_REQUEST` | 400 |
| API-E003 | ticker 길이 오류 (5자리) | `{"ticker": "00593"}` | `INVALID_REQUEST` | 400 |
| API-E004 | 동일 종목 재추가 | 이미 등록된 ticker | `INVALID_REQUEST` (UNIQUE 위반) | 409 |

### 1.2 GET /holdings — 보유 종목 목록

| ID | 시나리오 | 기대 응답 | 상태 |
|----|---------|---------|-----|
| API-003 | 보유 0개 | `{"items": [], "total": 0}` | 200 |
| API-004 | 보유 N개 | `HoldingListResponse` (items 배열) | 200 |

### 1.3 DELETE /holdings/{ticker}

| ID | 시나리오 | 기대 결과 | 상태 |
|----|---------|---------|-----|
| API-005 | 존재하는 종목 제거 | (empty body) | 204 |
| API-E005 | 존재하지 않는 종목 | `HOLDING_NOT_FOUND` | 404 |
| API-E006 | ticker 형식 오류 | `INVALID_REQUEST` | 400 |

> 신규 에러 코드 `HOLDING_NOT_FOUND`를 `API_CONTRACT_SKILL` 표준 목록에 추가 필요.

### 1.4 GET /recommendations?date=YYYY-MM-DD

| ID | 시나리오 | 기대 응답 | 상태 |
|----|---------|---------|-----|
| API-006 | 추천 존재일 | `RecommendationListResponse` (items + date) | 200 |
| API-007 | 추천 없는 날짜 | `{"items": [], "total": 0, "date": "..."}` | 200 |
| API-E007 | date 누락 | `MISSING_FIELD` | 400 |
| API-E008 | date 형식 오류 (`2026/04/25`) | `INVALID_REQUEST` | 400 |

### 1.5 GET /recommendations/recent?limit=N

| ID | 시나리오 | 기대 응답 | 상태 |
|----|---------|---------|-----|
| API-008 | limit=7 (기본) | 최근 7일 추천 | 200 |
| API-009 | limit=1 | 가장 최근 1일 | 200 |
| API-E009 | limit 음수 또는 100 초과 | `INVALID_REQUEST` | 400 |

### 1.6 GET /jobs/{job_id}

| ID | 시나리오 | 기대 결과 | 상태 |
|----|---------|---------|-----|
| API-010 | 존재하는 job | `JobStatusResponse` | 200 |
| API-E010 | 존재하지 않는 UUID | `JOB_NOT_FOUND` | 404 |
| API-E011 | 잘못된 UUID 형식 | `INVALID_REQUEST` | 400 |

### 1.7 GET /health

| ID | 시나리오 | 기대 응답 | 상태 |
|----|---------|---------|-----|
| API-011 | 헬스체크 | `{"status": "ok"}` | 200 |

### 1.8 공통 헤더 검증 (모든 엔드포인트)

| ID | 시나리오 | 기대 결과 |
|----|---------|---------|
| API-H001 | X-Request-ID 없이 요청 | 서버가 자동 생성 → 응답 헤더 포함 |
| API-H002 | X-Request-ID 포함 | 동일 값 에코 |
| API-H003 | 응답에 X-Response-Time 포함 | 정수 ms 값 |

---

## 2. Worker 테스트 사양

### 2.1 worker-data-collector

**입력 토픽**: `stock.data.requested` / **출력 토픽**: `stock.data.completed`, `stock.data.failed`

**정상 케이스:**

| ID | 시나리오 | 검증 포인트 |
|----|---------|-----------|
| WRK-001 | 정상 메시지 수신 → 전 흐름 완료 | jobs.status="completed", `stock.data.completed` 발행 |
| WRK-002 | 한투 API 호출 → signals INSERT | 종목별 row 존재, agency_net_buy/foreign_net_buy 계산 |
| WRK-003 | consecutive_buy_days 계산 | 이전 5일 데이터로 연속 매수일 누적 |
| WRK-004 | yfinance → macro_indicators INSERT | 5지표 모두 채워짐 |
| WRK-005 | 네이버 → news INSERT | 신호 종목별 뉴스 ≥ 0건 |
| WRK-006 | 신호 종목 필터 (3일 이상 연속) | `stock.data.completed.signals_count` = 신호 종목 수 |

**에러 케이스:**

| ID | 시나리오 | 기대 결과 |
|----|---------|---------|
| WRK-E001 | 한투 API 인증 실패 (401) | `stock.data.failed` 발행, jobs.status="failed", 로그 `worker_failed` |
| WRK-E002 | yfinance 일시 장애 | macro 없이 진행 (`macro_collected=false`), warning 로그, 작업 계속 |
| WRK-E003 | 네이버 차단 감지 (HTTP 429/403) | 해당 종목 뉴스 스킵 + `naver_scrape_blocked` warning, 작업 계속 |
| WRK-E004 | 메시지 스키마 불일치 (필수 필드 누락) | 즉시 `stock.data.failed`, 재시도 없음 |
| WRK-E005 | PG 연결 실패 | retry 3회 후 실패 → DLQ |

### 2.2 worker-telegram-notifier

**입력 토픽**: `stock.recommendation.completed` / **출력 토픽**: `stock.notify.completed`, `stock.notify.failed`

**정상 케이스:**

| ID | 시나리오 | 검증 포인트 |
|----|---------|-----------|
| WRK-007 | 추천 5개 정상 송신 | 텔레그램 message_id 반환, `stock.notify.completed` 발행 |
| WRK-008 | 추천 메시지 헤더에 "다음 거래일" 명시 | 메시지에 `target_trading_date` 포함 |
| WRK-009 | 매수 헬지 종목 — 추정 매집가 표시 | `estimated_avg_price` 라인 존재 |
| WRK-010 | 탈출 경보 종목 — "보유" 표기 + 익절/손절 안내 | "🔴 탈출 경보 (1종목 — 보유)" 형식 |
| WRK-011 | 추천 0개일 때 | "오늘 조건 충족 종목 없음" 메시지 송신 (PRD §18.1) |
| WRK-012 | 알림 하단 면책 문구 | "최종 판단은 본인이 직접 하세요" 포함 |

**에러 케이스:**

| ID | 시나리오 | 기대 결과 |
|----|---------|---------|
| WRK-E006 | 텔레그램 봇 토큰 무효 (401) | `stock.notify.failed`, ERROR 로그 |
| WRK-E007 | chat_id 무효 | `stock.notify.failed` |
| WRK-E008 | 텔레그램 API rate limit (429) | retry-after 준수 후 재시도 |
| WRK-E009 | PG 추천 조회 실패 | DLQ |

### 2.3 worker-telegram-listener

**입력**: 텔레그램 long-polling / **출력**: Backend FastAPI HTTP 호출 + 사용자 응답 메시지

**정상 케이스:**

| ID | 시나리오 | 동작 | 검증 |
|----|---------|------|-----|
| WRK-013 | `/start` | 환영 메시지 + 명령어 안내 | 응답 메시지 송신 |
| WRK-014 | `/help` | 도움말 송신 | 모든 명령어 설명 포함 |
| WRK-015 | `/add 005930` | `POST /holdings` 호출 → "추가됨: 삼성전자(005930)" | Backend 호출 + 응답 |
| WRK-016 | `/remove 005930` | `DELETE /holdings/005930` → "제거됨" | Backend 호출 + 응답 |
| WRK-017 | `/list` | `GET /holdings` → 보유 목록 메시지 | Backend 호출 + 응답 |
| WRK-018 | `/recent` | `GET /recommendations/recent?limit=7` → 최근 추천 메시지 | Backend 호출 + 응답 |
| WRK-019 | `/recent 2026-04-25` | `GET /recommendations?date=2026-04-25` → 해당 날짜 추천 | Backend 호출 + 응답 |

**에러 케이스:**

| ID | 시나리오 | 기대 결과 |
|----|---------|---------|
| WRK-E010 | 인가되지 않은 chat_id 메시지 | 무시 (응답 X), `command_unauthorized` warning |
| WRK-E011 | `/add abc` (형식 오류) | "종목코드는 6자리 숫자입니다" 에러 메시지 |
| WRK-E012 | Backend 호출 실패 (5xx) | "잠시 후 다시 시도해주세요" 메시지 |
| WRK-E013 | 알 수 없는 명령어 (`/foo`) | "/help를 참조하세요" 메시지 |

---

## 3. CrewAI 테스트 사양

### StockRecommendationCrew

**입력 토픽**: `stock.data.completed` / **출력 토픽**: `stock.recommendation.completed`, `stock.recommendation.failed`

**정상 케이스 (mock LLM 응답):**

| ID | 시나리오 | 검증 포인트 |
|----|---------|-----------|
| CREW-001 | 정상 Crew 실행 | 4 Agent 순차 실행, recommendations INSERT, `stock.recommendation.completed` 발행 |
| CREW-002 | 5종목 한도 준수 | recommendation_count ≤ 5 |
| CREW-003 | buy_hedge score ≥ 70 | 단계별 score 범위 검증 |
| CREW-004 | watch score 50~69 | |
| CREW-005 | exit_alert score < 50 + 보유 종목 한정 | holdings 테이블에 있는 ticker만 exit_alert 분류 |
| CREW-006 | 매수 헬지에 estimated_avg_price 산출 | 다른 단계는 None |
| CREW-007 | SignalQueryTool / NewsQueryTool / MacroQueryTool 호출 | 각 Tool 호출 1회 이상 |
| CREW-008 | HoldingsQueryTool 호출 (Synthesizer) | 보유 종목 조회 검증 |
| CREW-009 | RecommendationSaveTool로 PG 저장 | recommendations 테이블 row 존재 |

**에러 케이스:**

| ID | 시나리오 | 기대 결과 |
|----|---------|---------|
| CREW-E001 | LLM 응답 파싱 실패 | retry 후 DLQ → `stock.recommendation.failed` |
| CREW-E002 | Tool 실행 실패 | BaseTool 표준대로 에러 문자열 반환, Agent가 처리 |
| CREW-E003 | 신호 종목 0개 (3일 이상 연속 매수 없음) | recommendations 0건 저장 + `stock.recommendation.completed` (count=0) |
| CREW-E004 | PG 연결 실패 | DLQ |

---

## 4. 통합 테스트 사양

> Docker 전체 기동 상태에서 실행. 외부 API는 mock 또는 sandbox 사용.

| ID | 시나리오 | 시작점 | 종료점 | 검증 |
|----|---------|-------|-------|------|
| INT-001 | scheduler → data-collector | scheduler 트리거 | `stock.data.completed` 수신 | jobs.status 변화, signals/news/macro INSERT |
| INT-002 | data-collector → crewai | `stock.data.completed` 발행 | `stock.recommendation.completed` 수신 | recommendations INSERT |
| INT-003 | crewai → telegram-notifier | `stock.recommendation.completed` 발행 | `stock.notify.completed` 수신 | 텔레그램 메시지 송신 (mock bot으로 검증) |
| INT-004 | 전체 파이프라인 job_id Correlation | scheduler 트리거 | 텔레그램 송신 완료 | Grafana에서 동일 job_id로 전체 흐름 로그 추적 가능 |
| INT-005 | 텔레그램 명령어 흐름 | `/add 005930` (mock) | holdings 테이블 INSERT + 응답 메시지 | listener → backend → PG |

**장애 시나리오:**

| ID | 시나리오 | 장애 조건 | 기대 결과 |
|----|---------|----------|---------|
| INT-E001 | Kafka 일시 중단 | kafka 컨테이너 stop → 30초 후 start | 재연결, 미처리 메시지 처리 |
| INT-E002 | PG 일시 중단 | postgres 컨테이너 stop → start | 모든 서비스 재연결 (auto reconnect) |
| INT-E003 | 외부 API 모두 장애 | 한투/yfinance/네이버 mock 응답 5xx | data-collector → `stock.data.failed`, 알림 미발송 |
| INT-E004 | 휴장일 처리 | scheduler가 휴장일 감지 | Kafka 발행 자체 스킵, 알림 없음 |

---

## 5. E2E 시나리오

| ID | 시나리오 | 사전 조건 | 실행 단계 | 최종 검증 |
|----|---------|----------|----------|---------|
| E2E-001 | 신규 사용자 첫 사용 | Docker 기동, mock 외부 API | 1) `/start` 2) `/add 005930` 3) scheduler 트리거 4) 알림 수신 | 텔레그램에 추천 메시지 도착 |
| E2E-002 | 추천 0개인 날 | 신호 종목 없도록 mock 데이터 | scheduler 트리거 → 알림 수신 | "오늘 조건 충족 종목 없음" 메시지 |
| E2E-003 | 보유 종목 매수→매도 전환 | holdings 등록, 매도 전환 mock 데이터 | scheduler 트리거 → 알림 수신 | 🔴 탈출 경보 메시지 + "보유" 표기 |
| E2E-004 | 휴장일 알림 미수신 | KRX 휴장일로 시스템 시각 설정 | scheduler 트리거 | 알림 수신 X, 로그 `scheduler_skipped_holiday` |
| E2E-005 | 추천 이력 조회 | 과거 추천 데이터 존재 | `/recent` | 최근 7일 추천 메시지 수신 |

---

## 6. Grafana 로그 검증 기준

> 모든 테스트에서 다음을 추가 검증한다.

```
1. 테스트 사용 job_id로 Grafana 검색 → 전체 흐름 로그 존재
   {service=~"scheduler|worker-.*|crewai|backend"} |= "<job_id>"
2. 단계별 step_*_start / step_*_complete 쌍 존재
3. ERROR 레벨 로그가 예상된 에러 케이스에서만 발생
4. 비정상 종료 시 worker_failed / crew_failed 로그 존재
5. structlog JSON 파싱 성공 (level / job_id / event 라벨 추출 확인)
```

---

## 7. 테스트 환경 요구사항

- Docker Compose 전체 기동 (단, 단위 테스트는 컨테이너 외부 가능)
- `.env.test` 환경변수 (테스트 전용 DB / mock 토큰)
- 테스트 DB: 매 테스트마다 트랜잭션 롤백 또는 truncate
- mock LLM: pytest fixture로 OpenAI 응답 고정 (실제 API 호출 금지)
- mock 한투/yfinance/네이버: `responses` 또는 `httpx-mock` 사용
- mock 텔레그램 봇: `python-telegram-bot`의 `TestBot` 또는 자체 mock

---

## 8. 테스트 ID 규칙

```
API-{NNN}      API 엔드포인트 정상 케이스
API-E{NNN}     API 에러 케이스
API-H{NNN}     API 헤더 검증
WRK-{NNN}      Worker 정상 케이스 (data-collector / telegram-notifier / telegram-listener 통합 ID 공간)
WRK-E{NNN}     Worker 에러 케이스
CREW-{NNN}     CrewAI 정상 케이스
CREW-E{NNN}    CrewAI 에러 케이스
INT-{NNN}      통합 정상 케이스
INT-E{NNN}     통합 장애 시나리오
E2E-{NNN}      E2E 시나리오
```

---

## 9. 본 SPEC에서 도출된 신규 에러 코드

| error_code | HTTP | 추가 위치 | 사유 |
|-----------|------|---------|-----|
| `HOLDING_NOT_FOUND` | 404 | `shared/schemas/common` 또는 backend 예외 모듈 | DELETE /holdings/{ticker} 미존재 시 |

> `API_CONTRACT_SKILL.md` 표준 에러 코드 목록에 본 코드 추가 필요. Architect 후속 작업으로 처리.

---

## 10. 누락 시 본 SPEC 갱신 절차

QA Engineer가 구현 중 본 SPEC에 누락된 케이스를 발견하면:
1. 임의 추가하지 말고 Architect에게 이관
2. Architect가 본 파일을 갱신 후 신규 ID 부여
3. CONTEXT.md의 "다음 작업 시 주의사항"에 변경 이력 기록
