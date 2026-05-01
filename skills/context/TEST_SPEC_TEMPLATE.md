# TEST_SPEC.md — {프로젝트명}

> 이 문서는 Architect가 설계 단계에서 작성하는 테스트사양서다.
> 구현 전에 "무엇이 정상이고 무엇이 비정상인지"를 정의한다.
> QA Engineer는 이 사양서를 기반으로 테스트 코드를 작성한다.
> 각 엔지니어는 이 사양서를 참조만 하고, 테스트 코드는 작성하지 않는다.
> 마지막 업데이트: {YYYY-MM-DD HH:MM}

---

## 테스트 범위 요약

| 계층 | 대상 | 테스트 유형 | 수량 |
|------|------|-----------|------|
| API | FastAPI 엔드포인트 | 단위 | {N}개 |
| Worker | Kafka Consumer 처리 | 단위 | {N}개 |
| CrewAI | Crew/Agent 동작 | 단위 (mock) | {N}개 |
| Mobile | Provider/Widget | 단위 | {N}개 |
| 통합 | 모듈 간 연동 | 통합 | {N}개 |
| E2E | 전체 시나리오 | E2E | {N}개 |

---

## 1. API 엔드포인트 테스트 사양

> 각 엔드포인트별로 정상 케이스와 에러 케이스를 정의한다.
> API_CONTRACT_SKILL.md의 에러 코드를 기준으로 작성한다.

### POST /reels/generate (예시)

**정상 케이스:**

| ID | 시나리오 | 입력 | 기대 응답 | 기대 상태코드 |
|----|---------|------|----------|-------------|
| API-001 | 유효한 영상 업로드 | video: mp4, guideline_id: 유효값 | job_id 반환, status: "queued" | 202 |
| API-002 | 최소 크기 영상 | video: 1KB mp4 | job_id 반환 | 202 |

**에러 케이스:**

| ID | 시나리오 | 입력 | 기대 error_code | 기대 상태코드 |
|----|---------|------|----------------|-------------|
| API-E001 | 영상 없이 요청 | video 누락 | MISSING_FIELD | 400 |
| API-E002 | 허용되지 않는 형식 | video: txt 파일 | INVALID_FILE_TYPE | 400 |
| API-E003 | 크기 초과 | video: 500MB+ | VIDEO_TOO_LARGE | 400 |
| API-E004 | 존재하지 않는 가이드라인 | guideline_id: 없는값 | GUIDELINE_NOT_FOUND | 404 |

**공통 헤더 검증:**

| ID | 시나리오 | 기대 결과 |
|----|---------|----------|
| API-H001 | X-Request-ID 없이 요청 | 서버가 자동 생성하여 응답 헤더에 포함 |
| API-H002 | X-Request-ID 포함 요청 | 동일한 값이 응답 헤더에 반환 |

### GET /jobs/{job_id}

**정상 케이스:**

| ID | 시나리오 | 기대 응답 | 기대 상태코드 |
|----|---------|----------|-------------|
| API-003 | 존재하는 job 조회 | job 상태 정보 (status, progress 등) | 200 |

**에러 케이스:**

| ID | 시나리오 | 기대 error_code | 기대 상태코드 |
|----|---------|----------------|-------------|
| API-E005 | 존재하지 않는 job_id | JOB_NOT_FOUND | 404 |

---

## 2. Worker 처리 테스트 사양

> 각 Worker의 Kafka 메시지 수신 → 처리 → 결과 저장 흐름을 정의한다.

### {worker_name} Worker

**정상 케이스:**

| ID | 시나리오 | 입력 메시지 | 기대 결과 | 검증 포인트 |
|----|---------|-----------|----------|-----------|
| WRK-001 | 정상 처리 | topic: {토픽}, payload: {구조} | jobs.status = "completed" | MinIO에 결과물 존재, DB status 업데이트 |
| WRK-002 | 진행률 업데이트 | 동일 | 처리 중 progress 증가 | jobs.progress가 0→100 순차 증가 |

**에러 케이스:**

| ID | 시나리오 | 입력 | 기대 결과 | 검증 포인트 |
|----|---------|------|----------|-----------|
| WRK-E001 | MinIO 파일 없음 | 존재하지 않는 video_key | jobs.status = "failed" | DLQ 발행, 에러 메시지 DB 기록 |
| WRK-E002 | 처리 중 예외 | 손상된 파일 | retry_count < 3이면 재시도 | 재시도 후에도 실패 시 DLQ |
| WRK-E003 | Kafka 메시지 스키마 불일치 | 필수 필드 누락 | 즉시 DLQ (재시도 없음) | 치명적 에러로 분류 |

---

## 3. CrewAI 테스트 사양

> Agent/Task/Crew의 동작을 mock LLM 기반으로 검증한다.

### {crew_name} Crew

**정상 케이스:**

| ID | 시나리오 | 입력 | 기대 결과 | 검증 포인트 |
|----|---------|------|----------|-----------|
| CREW-001 | 정상 Crew 실행 | 유효 inputs | 모든 Task 순차 완료 | 각 Agent step 로그 존재, 최종 결과 MinIO 저장 |
| CREW-002 | 체크포인트 저장 | 동일 | 각 Task 후 체크포인트 | MinIO checkpoints 버킷에 파일 존재 |

**에러 케이스:**

| ID | 시나리오 | 입력 | 기대 결과 | 검증 포인트 |
|----|---------|------|----------|-----------|
| CREW-E001 | Agent 타임아웃 | LLM 응답 지연 | DLQ 발행 | jobs.status = "failed", 에러 로그 |
| CREW-E002 | Tool 실행 실패 | Tool이 에러 반환 | Agent가 에러 문자열 수신 | BaseTool 에러 문자열 반환 규칙 준수 |

---

## 4. Mobile 테스트 사양

> Flutter Provider/Widget의 상태 변화를 검증한다.

### Realtime 구독 동작

| ID | 시나리오 | 트리거 | 기대 UI 상태 |
|----|---------|-------|------------|
| MOB-001 | Job 제출 직후 | POST 응답 수신 | "대기 중..." 인디케이터 표시 |
| MOB-002 | 진행률 수신 | Realtime: status=processing, progress=45 | 진행률 바 45% 표시 |
| MOB-003 | 완료 수신 | Realtime: status=completed | 결과 화면 전환 |
| MOB-004 | 실패 수신 | Realtime: status=failed | 에러 메시지 + 재시도 버튼 |

### 에러 핸들링

| ID | 시나리오 | 트리거 | 기대 동작 |
|----|---------|-------|----------|
| MOB-E001 | 토큰 만료 | API 응답: TOKEN_EXPIRED | 토큰 갱신 후 재요청 |
| MOB-E002 | 인증 실패 | API 응답: UNAUTHORIZED | 로그인 화면 이동 |
| MOB-E003 | 네트워크 오류 | 연결 실패 | 에러 메시지 표시 |
| MOB-E004 | presigned URL 만료 | 결과 로드 실패 | 자동 재호출 |

---

## 5. 통합 테스트 사양

> Docker 기동 상태에서 모듈 간 실제 연동을 검증한다.

### 핵심 플로우

| ID | 시나리오 | 시작점 | 종료점 | 검증 포인트 |
|----|---------|-------|-------|-----------|
| INT-001 | 정상 전체 플로우 | POST /reels/generate | Realtime completed 수신 | DB status 변화 추적 (queued→processing→completed) |
| INT-002 | Kafka → Worker 연동 | Kafka 메시지 발행 | Worker 처리 완료 | Worker 로그에 job_id 존재, DB 업데이트 |
| INT-003 | Worker → Realtime 연동 | Worker status 업데이트 | Flutter Realtime 수신 | jobs 테이블 변경 → Realtime 이벤트 발생 |

### 장애 시나리오

| ID | 시나리오 | 장애 조건 | 기대 결과 |
|----|---------|----------|----------|
| INT-E001 | Kafka 일시 중단 | Kafka 컨테이너 stop → start | 재연결 후 미처리 메시지 처리 |
| INT-E002 | Worker 재시작 | Worker 컨테이너 restart | 미완료 job 재처리 (DLQ 또는 재시도) |
| INT-E003 | MinIO 접근 불가 | MinIO 컨테이너 stop | SERVICE_UNAVAILABLE 에러, 재시도 대기 |

---

## 6. E2E 시나리오

> 사용자 관점의 전체 시나리오. Flutter → Backend → Worker → 결과 수신까지.

| ID | 시나리오 | 사전 조건 | 실행 단계 | 최종 검증 |
|----|---------|----------|----------|----------|
| E2E-001 | 정상 작업 완료 | 전체 서비스 기동 | 1. 파일 업로드 2. job_id 수신 3. 진행률 확인 4. 완료 결과 확인 | 결과 파일 다운로드 가능 |
| E2E-002 | 작업 실패 후 재시도 | 전체 서비스 기동 | 1. 의도적 실패 입력 2. 실패 확인 3. 재시도 4. 성공 확인 | 두 번째 시도 정상 완료 |

---

## Grafana 로그 검증 기준

> 모든 테스트에서 Grafana 로그 검증은 필수다.

```
테스트 통과 조건:
1. 테스트에서 사용한 job_id로 Grafana 조회 시 전체 흐름 로그 존재
2. ERROR 레벨 로그가 예상된 에러 케이스에서만 발생
3. 각 단계의 step_*_start / step_*_complete 쌍이 빠짐없이 존재
4. 비정상 종료 시 crew_failed / worker_failed 로그 존재
```

---

## 테스트 환경 요구사항

```
- Docker Compose 전체 기동 상태
- .env.test 환경변수 파일 (테스트용 설정)
- 테스트 전용 DB 초기화 (각 테스트 독립 실행 보장)
- mock LLM 설정 (CrewAI 테스트용 — 실제 API 호출 금지)
```

---

## 테스트 ID 규칙

```
API-{NNN}      API 엔드포인트 정상 케이스
API-E{NNN}     API 에러 케이스
API-H{NNN}     API 헤더 검증
WRK-{NNN}      Worker 정상 케이스
WRK-E{NNN}     Worker 에러 케이스
CREW-{NNN}     CrewAI 정상 케이스
CREW-E{NNN}    CrewAI 에러 케이스
MOB-{NNN}      Mobile 정상 케이스
MOB-E{NNN}     Mobile 에러 케이스
INT-{NNN}      통합 정상 케이스
INT-E{NNN}     통합 장애 시나리오
E2E-{NNN}      E2E 시나리오
```
