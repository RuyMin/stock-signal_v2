---
name: vibe-framework-fastapi
description: >
  Vibe Framework의 FastAPI 백엔드 게이트웨이 구조 및 코딩 규칙.
  FastAPI 엔드포인트, 라우터, 스키마 작성 시 반드시 참조.
  FastAPI에서 직접 AI 처리 또는 영상 처리를 하는 코드는 절대 생성 금지.
---

# FastAPI Skill

## 핵심 책임

FastAPI 게이트웨이는 **오직 이 세 가지만** 담당한다.

1. 요청 수신 및 검증
2. Kafka에 Job 발행 (즉시 job_id 반환)
3. Job 상태 조회 응답

**AI 처리, 영상 처리, 파일 변환은 절대 FastAPI에서 하지 않는다.**

---

## 디렉토리 구조

```
backend/
├── Dockerfile
├── entrypoint.sh            # alembic upgrade head → uvicorn 시작
├── alembic.ini              # Alembic 설정
├── requirements.txt
├── main.py                  # FastAPI 앱 초기화
├── core/
│   ├── config.py            # 환경변수 (pydantic-settings)
│   ├── auth.py              # Supabase JWT 검증 (SUPABASE_SKILL.md 참조)
│   ├── database.py          # SQLAlchemy 연결
│   ├── kafka.py             # Kafka Producer
│   └── dependencies.py      # FastAPI Depends 모음 (get_current_user 포함)
├── routers/
│   ├── health.py            # 헬스체크
│   ├── jobs.py              # Job 상태 조회 (공통)
│   └── {feature_name}.py    # 기능별 라우터
├── models/                  # SQLAlchemy ORM 모델
│   └── job.py
├── migrations/              # Alembic 마이그레이션
│   ├── env.py
│   └── versions/
└── schemas/                 # Pydantic 요청/응답 스키마
    └── {feature_name}.py
```

---

## 환경 설정 (config.py)

```python
# core/config.py
from pydantic_settings import BaseSettings
from enum import Enum

class VibeEnv(str, Enum):
    dev = "dev"
    staging = "staging"
    prod = "prod"

class Settings(BaseSettings):
    # 환경 식별
    VIBE_ENV: VibeEnv = VibeEnv.dev

    # PostgreSQL
    DATABASE_URL: str

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"

    # MinIO
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str
    MINIO_BUCKET_PREFIX: str = "dev"

    # Redis
    REDIS_URL: str = "redis://redis:6379"

    # Supabase
    SUPABASE_JWT_SECRET: str

    # CORS
    CORS_ORIGINS: str = "*"

    # 로그
    BACKEND_LOG_LEVEL: str = "DEBUG"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def is_dev(self) -> bool:
        return self.VIBE_ENV == VibeEnv.dev

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## 표준 엔드포인트 패턴

```python
# routers/reels.py
from fastapi import APIRouter, Depends, UploadFile, File
from core.kafka import get_kafka_producer
from core.dependencies import get_db
from schemas.reels import ReelsGenerateRequest, JobResponse
import uuid

router = APIRouter(prefix="/reels", tags=["reels"])

@router.post("/generate", response_model=JobResponse)
async def generate_reels(
    video: UploadFile = File(...),
    guideline_id: str = Form(...),
    producer = Depends(get_kafka_producer),
    db = Depends(get_db),
):
    # 1. 파일 MinIO 업로드
    object_key = f"raw-uploads/{uuid.uuid4()}/{video.filename}"
    await upload_to_minio(video, object_key)

    # 2. Job 생성 (PostgreSQL)
    job_id = str(uuid.uuid4())
    await create_job(db, job_id, "reels_generation")

    # 3. Kafka 발행 — 즉시 반환
    await producer.send(
        "reels.generation.requested",
        {
            "job_id": job_id,
            "video_key": object_key,
            "guideline_id": guideline_id,
        }
    )

    return JobResponse(job_id=job_id, status="queued")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, db = Depends(get_db)):
    job = await get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse.from_orm(job)
```

---

## config.py 표준

```python
# core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    database_url: str
    
    # Redis
    redis_url: str
    
    # Kafka
    kafka_bootstrap_servers: str
    
    # MinIO
    minio_endpoint: str
    minio_root_user: str
    minio_root_password: str
    minio_bucket_raw: str = "raw-uploads"
    minio_bucket_output: str = "processed-outputs"
    
    # AI
    openai_api_key: str
    anthropic_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## Kafka Producer 표준

```python
# core/kafka.py
from aiokafka import AIOKafkaProducer
import json

_producer = None

async def get_kafka_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        )
        await _producer.start()
    return _producer
```

---

## 응답 스키마 표준

```python
# schemas/common.py — 모든 라우터에서 재사용
from pydantic import BaseModel
from typing import Optional, Literal

class JobResponse(BaseModel):
    """Job 제출 즉시 응답 — 항상 이 형태로 반환"""
    job_id: str
    status: Literal["queued"] = "queued"
    message: str = "Job queued successfully"

class JobStatusResponse(BaseModel):
    """Job 상태 조회 응답"""
    job_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    progress: int  # 0-100
    result_url: Optional[str] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True
```

---

## PostgreSQL Job 테이블 표준

```sql
-- infra/postgres/init.sql
CREATE TABLE jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type    VARCHAR(100) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'queued',
    progress    INTEGER NOT NULL DEFAULT 0,
    result_url  TEXT,
    error_msg   TEXT,
    metadata    JSONB,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_jobs_status ON jobs(status);
```

---

## DB 커넥션 풀 설정

> 상세 설정값 → `infra/PERFORMANCE_SKILL.md` 섹션 1 참조.

```python
# core/database.py — pool_size 미설정 시 기본 5로 병목 발생
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,             # ← PERFORMANCE_SKILL 권장값
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)
```

---

## requirements.txt 표준

```
fastapi==0.110.0
uvicorn[standard]==0.29.0
pydantic==2.7.0
pydantic-settings==2.2.0
sqlalchemy==2.0.29
asyncpg==0.29.0
alembic==1.13.0
aiokafka==0.10.0
boto3==1.34.0
redis==5.0.0
PyJWT==2.8.0
python-multipart==0.0.9
```

---

## 금지 패턴

```python
# ❌ FastAPI에서 직접 CrewAI 실행
@app.post("/generate")
async def generate(video: UploadFile):
    crew = ReelsGenerationCrew()
    result = crew.kickoff(...)  # 절대 금지

# ❌ FastAPI에서 FFmpeg 실행
@app.post("/process")
async def process(video: UploadFile):
    subprocess.run(["ffmpeg", ...])  # 절대 금지

# ❌ 파일을 메모리에 들고 처리
@app.post("/upload")
async def upload(video: UploadFile):
    content = await video.read()  # 전체 읽기 금지
    process_in_memory(content)    # 메모리 처리 금지
```

---

## entrypoint.sh 표준

```bash
#!/bin/bash
set -e

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting FastAPI server..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
```

Dockerfile에서 이 스크립트를 ENTRYPOINT로 설정한다.

```dockerfile
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
```
