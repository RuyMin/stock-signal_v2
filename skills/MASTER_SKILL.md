---
name: vibe-framework-master
description: >
  바이브코딩 전용 완전 모듈형 풀스택 프레임워크의 마스터 설계 문서.
  Flutter 모바일 앱, Python FastAPI 백엔드, CrewAI AI 오케스트레이션,
  Docker 완전 컨테이너화를 포함하는 모든 프로젝트에 반드시 적용.
  새 프로젝트 시작, 기능 추가, 에러 수정, 구조 설계 시 항상 이 문서를 먼저 읽어야 함.
  이 프레임워크를 벗어나는 구조는 절대 생성하지 말 것.
---

# Vibe Framework — Master Skill

## 프레임워크 철학

이 프레임워크는 **기획서만으로 일관된 퀄리티의 풀스택 앱을 바이브코딩**하기 위해 설계되었다.
사람이 코드에 직접 관여하지 않으므로, Claude는 아래 원칙을 반드시 지켜야 한다.

### 핵심 원칙

1. **모듈 경계 절대 준수** — 각 컨테이너는 자신의 책임 범위 밖의 코드를 절대 포함하지 않는다
2. **컨텍스트 파일 우선** — 코딩 전 반드시 `CONTEXT.md`를 읽고, 작업 후 반드시 업데이트한다
3. **Base 클래스 상속 강제** — 프레임워크 core 클래스를 우회하는 코드는 생성하지 않는다
4. **중간 산출물 항상 저장** — 메모리에만 존재하는 데이터는 허용하지 않는다. 반드시 MinIO 또는 PostgreSQL에 저장
5. **에러는 항상 Kafka로** — 재시도 가능한 모든 작업은 Kafka를 통해 처리한다
6. **인터페이스 스펙 고정** — 모듈 간 통신은 반드시 OpenAPI 스펙 또는 Pydantic 스키마로 정의된 것만 사용

---

## 전체 스택

| 역할 | 기술 | 비고 |
|------|------|------|
| 모바일 앱 | Flutter | iOS / Android |
| 백엔드 게이트웨이 | Python + FastAPI | 요청 수신 및 job 발행만 담당 |
| AI 오케스트레이션 | CrewAI | 반드시 Base 클래스 상속 |
| AI Workers | Python | FFmpeg, Whisper, OpenCV 등 |
| 메인 DB | PostgreSQL | 모든 상태 및 결과 저장 |
| 캐시 / 세션 | Redis | 중간 캐시, 세션 관리 |
| 메시지 큐 | Kafka + Zookeeper | 모든 비동기 작업 |
| 실시간 푸시 | Supabase Realtime | self-hosted |
| 파일 / 영상 저장 | MinIO | S3 호환, self-hosted |
| 로그 수집 | Promtail | 컨테이너 로그 수집 에이전트 |
| 로그 저장 | Loki | 로그 DB |
| 로그 시각화 | Grafana | 대시보드 + 알림 |
| 인프라 | Docker Compose | 모든 서비스 컨테이너화 |
| 웹 (추후) | 미정 | Flutter Web 또는 Next.js |

---

## 최상위 디렉토리 구조

```
{project-root}/
├── CONTEXT.md                  # 프로젝트 현재 상태 (반드시 최신 유지)
├── docker-compose.yml          # 전체 서비스 오케스트레이션
├── docker-compose.dev.yml      # 개발환경 오버라이드
├── .env.example                # 환경변수 템플릿 (실제 값 절대 커밋 금지)
│
├── mobile/                     # Flutter 앱
├── backend/                    # FastAPI 게이트웨이
├── workers/                    # AI Worker 컨테이너들
├── crewai/                     # CrewAI 오케스트레이션
├── infra/                      # 인프라 설정 파일들
│   ├── postgres/
│   ├── redis/
│   ├── kafka/
│   ├── minio/
│   └── supabase/
├── shared/                     # 공유 타입 / 스키마 정의
│   ├── schemas/                # Pydantic 모델 (Python)
│   └── models/                 # Dart 모델 (Flutter)
└── skills/                     # 이 프레임워크의 SKILL 문서들
```

> 상세 디렉토리 규칙 → `structure/DIRECTORY_SKILL.md`
> Docker 구성 규칙 → `structure/DOCKER_SKILL.md`

---

## 모듈 간 통신 규칙

```
Flutter  →  FastAPI  →  Kafka  →  CrewAI / Worker  →  결과저장  →  Supabase Realtime  →  Flutter
           (즉시 job_id 반환)      (비동기 처리)        (MinIO/PostgreSQL)    (완료 푸시)
```

### 절대 금지 패턴

```
# ❌ FastAPI에서 직접 AI 처리
@app.post("/generate")
async def generate(video: UploadFile):
    result = crew.kickoff()  # 절대 금지 — 타임아웃 발생

# ✅ 올바른 패턴
@app.post("/generate")
async def generate(video: UploadFile):
    job_id = await publish_to_kafka("video.process", payload)
    return {"job_id": job_id, "status": "queued"}
```

---

## CONTEXT.md 운용 규칙

모든 작업 시작 전 `CONTEXT.md`를 읽고, 작업 완료 후 반드시 아래 항목을 업데이트한다.

- 현재 구현된 모듈 목록과 상태
- 미구현 / 진행 중인 항목
- 모듈 간 인터페이스 변경 이력
- 최근 에러 및 해결 방법
- 다음 작업 시 주의사항

> CONTEXT.md 템플릿 → `context/CONTEXT_TEMPLATE.md`

---

## 새 프로젝트 시작 체크리스트

Claude는 새 프로젝트를 시작할 때 반드시 이 순서를 따른다.

1. `CONTEXT.md` 생성 (템플릿 기반)
2. `docker-compose.yml` 기본 구조 생성
3. `shared/schemas/` 에 핵심 Pydantic 모델 먼저 정의
4. `shared/models/` 에 대응하는 Dart 모델 생성
5. FastAPI 게이트웨이 엔드포인트 스펙 정의 (구현 전)
6. Kafka 토픽 목록 정의
7. 각 모듈 구현 시작

---

## 기능 추가 체크리스트

1. `CONTEXT.md` 읽기
2. 영향받는 모듈 파악
3. `shared/schemas/` 인터페이스 변경 여부 확인
4. 변경 시 Flutter 모델도 동기화
5. 기능 구현
6. `CONTEXT.md` 업데이트

---

## 로그 전략 원칙

> 상세 규칙 → `infra/LOGGING_SKILL.md` (코드 작성 전 반드시 읽을 것)

1. **모든 로그는 JSON 구조화 형식** — `print()` 절대 금지, `structlog` 사용
2. **모든 로그에 `job_id` 포함** — Correlation ID로 전체 흐름 추적
3. **모든 처리 단계마다 INFO 로그** — 어느 단계에서 멈췄는지 즉시 파악
4. **에러는 반드시 stack trace 포함** — `exc_info=True` 필수
5. **로그 수집은 Loki + Promtail + Grafana** — Docker Compose에 항상 포함

```
로그 흐름:
각 컨테이너 stdout (JSON) → Promtail → Loki → Grafana
Grafana에서 job_id 하나로 전체 흐름 타임라인 조회 가능
```

---

## 에러 처리 원칙

- 모든 Worker 에러는 PostgreSQL `job_errors` 테이블에 기록
- 재시도 가능한 에러는 Kafka Dead Letter Queue로 발행
- 치명적 에러는 Supabase Realtime으로 Flutter에 즉시 푸시
- 에러 발생 시 중간 산출물은 MinIO에 보존 (디버깅용)

> 에러 로그 템플릿 → `context/ERROR_LOG_TEMPLATE.md`

---

## 하위 SKILL 참조 가이드

| 작업 | 읽어야 할 SKILL |
|------|----------------|
| 프로젝트 시작 / Vault 연동 | `OBSIDIAN_SKILL.md` |
| 디렉토리 구조 생성/수정 | `structure/DIRECTORY_SKILL.md` |
| Docker Compose 작성 | `structure/DOCKER_SKILL.md` |
| Flutter 코드 작성 | `mobile/FLUTTER_SKILL.md` |
| FastAPI 코드 작성 | `backend/FASTAPI_SKILL.md` |
| Flutter ↔ FastAPI 헤더/에러 규칙 | `shared/API_CONTRACT_SKILL.md` |
| CrewAI Agent/Crew 작성 | `crewai/CREWAI_SKILL.md` |
| CrewAI Tool 작성 | `crewai/CREWAI_TOOL_SKILL.md` |
| Kafka 설정/사용 | `infra/KAFKA_SKILL.md` |
| 로그 전략/구현 | `infra/LOGGING_SKILL.md` |
| MinIO 파일 스토리지 | `infra/MINIO_SKILL.md` |
| Supabase Realtime + Auth | `infra/SUPABASE_SKILL.md` |
| 테스트 전략/작성 | `testing/TEST_SKILL.md` |
| 배포/CI·CD | `infra/DEPLOY_SKILL.md` |
| 성능 튜닝/스케일링 | `infra/PERFORMANCE_SKILL.md` |
