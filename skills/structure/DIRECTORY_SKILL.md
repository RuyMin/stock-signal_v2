---
name: vibe-framework-directory
description: >
  Vibe Framework의 디렉토리 구조 규칙.
  새 파일/폴더 생성, 모듈 추가, 코드 위치 결정 시 반드시 참조.
  이 규칙을 벗어나는 파일 위치는 허용하지 않음.
---

# Directory Structure Skill

## 전체 구조 상세

```
{project-root}/
│
├── CONTEXT.md
├── TEST_SPEC.md
├── docker-compose.yml
├── docker-compose.dev.yml
├── docker-compose.staging.yml
├── docker-compose.prod.yml
├── .env.example
├── .gitignore
│
├── .github/
│   └── workflows/
│       ├── ci.yml                       # PR 시 테스트
│       └── deploy.yml                   # main merge 시 빌드 + 배포
│
├── mobile/                          # Flutter 앱
│   ├── Dockerfile
│   ├── pubspec.yaml
│   └── lib/
│       ├── main.dart
│       ├── core/                    # 앱 전역 설정
│       │   ├── config/
│       │   ├── router/
│       │   └── theme/
│       ├── shared/                  # 공유 위젯/유틸
│       │   ├── widgets/
│       │   └── utils/
│       ├── models/                  # ← shared/models/ 에서 자동 생성
│       ├── services/                # API / Realtime 연결
│       │   ├── api_service.dart
│       │   └── realtime_service.dart
│       └── features/                # 기능별 모듈 (feature-first 구조)
│           └── {feature_name}/
│               ├── data/
│               ├── domain/
│               └── presentation/
│
├── backend/                         # FastAPI 게이트웨이
│   ├── Dockerfile
│   ├── entrypoint.sh                # alembic upgrade head → uvicorn
│   ├── alembic.ini
│   ├── requirements.txt
│   ├── main.py
│   ├── core/
│   │   ├── config.py                # 환경변수 로드
│   │   ├── auth.py                  # Supabase JWT 검증
│   │   ├── database.py              # PostgreSQL 연결
│   │   └── kafka.py                 # Kafka Producer
│   ├── routers/                     # 엔드포인트 (기능별 분리)
│   │   └── {feature_name}.py
│   ├── schemas/                     # ← shared/schemas/ 심링크 또는 복사
│   ├── models/                      # SQLAlchemy ORM 모델
│   └── migrations/                  # Alembic 마이그레이션
│       ├── env.py
│       └── versions/
│
├── workers/                         # AI Worker 컨테이너들
│   └── {worker_name}/               # 예: video_analyzer, script_writer
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── main.py                  # Kafka Consumer 진입점
│       ├── processor.py             # 핵심 처리 로직
│       └── tools/                   # 이 Worker 전용 도구
│
├── crewai/                          # CrewAI 오케스트레이션
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── core/                        # 프레임워크 레벨 — 수정 금지
│   │   ├── base_agent.py
│   │   ├── base_task.py
│   │   ├── base_crew.py
│   │   └── base_tool.py
│   ├── tools/                       # 재사용 공통 툴
│   │   ├── video_tools.py
│   │   ├── storage_tools.py
│   │   ├── llm_tools.py
│   │   └── whisper_tools.py
│   └── crews/                       # 프로젝트별 Crew
│       └── {crew_name}/
│           ├── agents.py
│           ├── tasks.py
│           └── crew.py
│
├── infra/                           # 인프라 설정
│   ├── postgres/
│   │   └── init.sql                 # 초기 스키마 (유일한 진실의 원천)
│   ├── redis/
│   │   └── redis.conf
│   ├── kafka/
│   │   └── topics.yml               # 토픽 목록 및 설정
│   ├── minio/
│   │   └── buckets.yml              # 버킷 초기화 설정
│   ├── nginx/
│   │   └── vibe.conf                # 리버스 프록시 설정 (운영용)
│   └── supabase/
│       └── config.toml
│
└── shared/                          # 모듈 간 공유 계약
    ├── schemas/                     # Pydantic 모델 (Python)
    │   ├── job.py                   # Job 상태 스키마
    │   ├── events.py                # Kafka 이벤트 스키마
    │   └── {feature_name}.py
    └── models/                      # Dart 모델 (Flutter)
        ├── job_model.dart
        └── {feature_name}_model.dart

tests/                               # 테스트 코드 (QA Engineer가 작성)
├── conftest.py                      # 공통 fixture
├── requirements.txt                 # 테스트 의존성
├── factories/                       # factory-boy 팩토리
├── unit/                            # 단위 테스트
│   ├── backend/                     # API 엔드포인트 테스트
│   ├── workers/                     # Worker 처리 테스트
│   └── crewai/                      # Crew/Agent 테스트
├── integration/                     # 모듈 간 통합 테스트
└── e2e/                             # E2E 시나리오 테스트
```

---

## 파일 위치 결정 규칙

### 새 기능 추가 시

```
질문: 이 코드는 어디에 속하는가?

Flutter UI / 상태관리    → mobile/features/{feature}/
Flutter API 호출        → mobile/services/
FastAPI 엔드포인트       → backend/routers/{feature}.py
FastAPI DB 모델         → backend/models/
AI 처리 로직            → workers/{worker_name}/ 또는 crewai/crews/
재사용 가능한 AI 툴      → crewai/tools/
모듈 간 데이터 구조      → shared/schemas/ + shared/models/
인프라 설정             → infra/{service}/
```

### 절대 금지 위치

```
❌ backend/에 영상 처리 코드
❌ workers/에 HTTP 엔드포인트
❌ crewai/core/에 프로젝트별 로직
❌ mobile/에 비즈니스 로직 (presentation 레이어 밖)
❌ 루트에 .env 파일 (반드시 .env.example만)
```

---

## 네이밍 컨벤션

| 대상 | 규칙 | 예시 |
|------|------|------|
| Python 파일 | snake_case | `video_analyzer.py` |
| Python 클래스 | PascalCase | `VideoAnalyzerAgent` |
| Dart 파일 | snake_case | `video_service.dart` |
| Dart 클래스 | PascalCase | `VideoService` |
| Docker 서비스명 | kebab-case | `video-worker` |
| Kafka 토픽 | dot.notation | `video.process.requested` |
| MinIO 버킷 | kebab-case | `raw-videos`, `processed-reels` |
| PostgreSQL 테이블 | snake_case | `job_errors` |
| 환경변수 | UPPER_SNAKE_CASE | `KAFKA_BOOTSTRAP_SERVERS` |

---

## shared/ 동기화 규칙

`shared/schemas/` 의 Pydantic 모델과 `shared/models/` 의 Dart 모델은 **항상 동기화**되어야 한다.

스키마 변경 시 반드시 두 파일 모두 수정한다.

```python
# shared/schemas/job.py
class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    progress: int  # 0-100
    result_url: Optional[str]
    error_message: Optional[str]
```

```dart
// shared/models/job_model.dart
class JobStatus {
  final String jobId;
  final String status;  // queued | processing | completed | failed
  final int progress;
  final String? resultUrl;
  final String? errorMessage;
}
```
