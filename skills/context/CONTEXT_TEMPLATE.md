# CONTEXT.md — {프로젝트명}

> 이 파일은 Claude의 프로젝트 기억 장치입니다.
> 모든 작업 시작 전 반드시 읽고, 작업 완료 후 반드시 업데이트하세요.
> 마지막 업데이트: {YYYY-MM-DD HH:MM}

---

## 프로젝트 개요

- **프로젝트명**: 
- **목적**: 
- **현재 단계**: `초기설정` / `개발중` / `테스트중` / `완료`

---

## 스택 확정

| 역할 | 기술 | 버전 | 상태 |
|------|------|------|------|
| 모바일 | Flutter | 3.x | ⬜ 미시작 |
| 백엔드 | FastAPI | 0.110 | ⬜ 미시작 |
| AI 오케스트레이션 | CrewAI | - | ⬜ 미시작 |
| DB | PostgreSQL | 16 | ⬜ 미시작 |
| 캐시 | Redis | 7 | ⬜ 미시작 |
| 메시지 큐 | Kafka | 7.6 | ⬜ 미시작 |
| 파일 저장 | MinIO | latest | ⬜ 미시작 |
| 실시간 | Supabase | - | ⬜ 미시작 |

상태 범례: ⬜ 미시작 / 🔄 진행중 / ✅ 완료 / ❌ 에러

---

## 모듈 구현 현황

### Backend (FastAPI)
- [ ] 기본 앱 구조
- [ ] PostgreSQL 연결
- [ ] Kafka Producer 연결
- [ ] MinIO 연결
- [ ] 엔드포인트: {목록}

### CrewAI
- [ ] core/ Base 클래스
- [ ] tools/ 공통 툴
- [ ] crews/{crew_name}/ Agent/Task/Crew

### Workers
- [ ] {worker_name}: {설명}

### Flutter
- [ ] 기본 앱 구조
- [ ] API Service 연결
- [ ] Realtime Service 연결
- [ ] Features: {목록}

---

## Kafka 토픽 목록

| 토픽명 | 발행자 | 구독자 | 용도 |
|--------|--------|--------|------|
| {feature}.process.requested | backend | crewai/worker | 작업 요청 |
| {feature}.process.completed | worker | supabase | 완료 알림 |
| {feature}.process.failed | worker | dlq-handler | 실패 DLQ |

---

## MinIO 버킷 목록

| 버킷명 | 용도 | 보존 기간 |
|--------|------|----------|
| raw-uploads | 사용자 업로드 원본 | 7일 |
| processed-outputs | 완성 결과물 | 30일 |
| checkpoints | 중간 산출물 | 3일 |

---

## API 엔드포인트 현황

| Method | Path | 상태 | 설명 |
|--------|------|------|------|
| POST | /reels/generate | ⬜ | 릴스 생성 요청 |
| GET | /jobs/{job_id} | ⬜ | Job 상태 조회 |

---

## 모듈 간 인터페이스

### Flutter → Backend

```
POST /reels/generate
Request: { video: File, guideline_id: string }
Response: { job_id: string, status: "queued" }
```

### Backend → Kafka

```
Topic: reels.generation.requested
Payload: { job_id, video_key, guideline_id }
```

### Worker → PostgreSQL

```
UPDATE jobs SET status, progress, result_url WHERE id = job_id
```

---

## API Contract 현황

> Architect가 API_CONTRACT_SKILL.md 기반으로 설계한 내용을 기록.
> Backend Engineer / Mobile Engineer가 참조하는 인터페이스 계약.

### 공통 응답 헤더

| 헤더 | 설명 |
|------|------|
| X-Request-ID | 요청 추적 ID (클라이언트가 보내거나 서버가 생성) |

### 에러 코드 목록

| error_code | HTTP Status | 설명 |
|------------|-------------|------|
| {CODE} | {STATUS} | {설명} |

### 공통 응답 구조

```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "human-readable message",
  "detail": {}
}
```

---

## 로그 이벤트 목록

> Architect가 설계 시 정의. Grafana 대시보드 기준.

| 서비스 | 이벤트명 | 레벨 | 설명 |
|--------|---------|------|------|
| backend | job_queued | INFO | Kafka 발행 완료 |
| backend | upload_complete | INFO | MinIO 업로드 완료 |
| backend | request_error | ERROR | 요청 검증 실패 |
| crewai | crew_started | INFO | Crew 시작 |
| crewai | agent_step_start | INFO | Agent 단계 시작 |
| crewai | agent_step_complete | INFO | Agent 단계 완료 |
| crewai | crew_failed | ERROR | Crew 실패 |
| worker-{name} | worker_received | INFO | Kafka 메시지 수신 |
| worker-{name} | step_{name}_start | INFO | 처리 단계 시작 |
| worker-{name} | step_{name}_complete | INFO | 처리 단계 완료 |
| worker-{name} | worker_failed | ERROR | 처리 실패 |

**Grafana URL**: http://localhost:3000
**핵심 추적 쿼리**: `{job="vibe-framework"} |= "{job_id}"`

---

## 테스트 완료 상태

> QA Engineer (모드 B) 테스트 실행 후 업데이트.

| 테스트 유형 | 상태 | 통과/전체 | 마지막 실행 |
|-----------|------|----------|-----------|
| 단위 (Backend) | {미실행/통과/실패} | {N}/{N} | {YYYY-MM-DD} |
| 단위 (Worker) | {미실행/통과/실패} | {N}/{N} | {YYYY-MM-DD} |
| 단위 (CrewAI) | {미실행/통과/실패} | {N}/{N} | {YYYY-MM-DD} |
| 단위 (Flutter) | {미실행/통과/실패} | {N}/{N} | {YYYY-MM-DD} |
| 통합 | {미실행/통과/실패} | {N}/{N} | {YYYY-MM-DD} |
| E2E | {미실행/통과/실패} | {N}/{N} | {YYYY-MM-DD} |

**실패 케이스 목록** (있는 경우):
- {TEST_SPEC ID}: {실패 사유}

---

## 최근 에러 이력

| 날짜 | 모듈 | 에러 요약 | 해결 방법 | 상태 |
|------|------|----------|----------|------|
| - | - | - | - | - |

---

## 다음 작업 시 주의사항

- (작업 완료 후 다음 Claude 세션을 위한 메모를 여기에 작성)

---

## 미결 사항 / 결정 필요

- [ ] {결정이 필요한 항목}
