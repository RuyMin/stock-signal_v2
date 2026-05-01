---
name: vibe-framework-performance
description: >
  Vibe Framework의 성능 튜닝 포인트와 스케일링 가이드.
  코드 생성 시 병목이 되는 기본값을 방지하기 위한 참조 문서.
  DB 커넥션 풀, Kafka 파티션, Worker 스케일링, MinIO 동시성 등.
---

# Performance Skill

## 핵심 원칙

1. **기본값을 그대로 쓰지 않는다** — 프레임워크에서 정의한 값을 사용한다
2. **병목은 항상 측정 후 튜닝한다** — 추측으로 최적화하지 않는다
3. **수평 확장이 가능한 구조를 유지한다** — Worker는 인스턴스 추가만으로 처리량 증가
4. **리소스 제한을 명시한다** — Docker의 메모리/CPU 제한을 설정한다

---

## 병목 포인트 요약

```
위치              병목 원인                     기본값 문제           해결
──────────────────────────────────────────────────────────────────────────
DB 커넥션 풀      동시 요청 > pool_size         SQLAlchemy 기본 5     서비스별 적정값 설정
Kafka 파티션      파티션 1개 → Worker 1개만     단일 파티션           요청 토픽 파티션 3+
Worker 스케일링   단일 인스턴스                  replicas: 1          파티션 수에 맞춰 확장
MinIO 업로드      대용량 파일 메모리 적재         전체 파일 read()     청크 스트리밍
Redis 커넥션      커넥션 폭발                    제한 없음            max_connections 설정
CrewAI LLM 호출   직렬 Agent 실행               기본 직렬             Task 병렬화 고려
```

---

## 1. PostgreSQL 커넥션 풀

### 서비스별 권장값

```
서비스           pool_size    max_overflow    이유
─────────────────────────────────────────────────────────
Backend (API)    10           5               동시 HTTP 요청 처리
Worker (각)      3            2               Job 처리는 순차적, DB 업데이트만
CrewAI           3            2               Worker와 동일
──────────────────────────────────────────────────────
합계 (최대)      ~25                          PostgreSQL max_connections=100 기본값에 여유
```

### SQLAlchemy 설정 코드

```python
# backend/core/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,             # 상시 유지 커넥션 수
    max_overflow=5,           # 초과 시 임시 커넥션 (15까지)
    pool_timeout=30,          # 커넥션 대기 최대 시간 (초)
    pool_recycle=1800,        # 30분마다 커넥션 재생성 (DB 타임아웃 방지)
    pool_pre_ping=True,       # 사용 전 커넥션 생존 확인
    echo=settings.is_dev,     # 개발 환경에서만 SQL 로그
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
```

```python
# workers/{name}/core/db.py
import asyncpg
import os

# Worker는 커넥션 풀을 사용한다 (매번 connect/close 금지)
_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            os.getenv("DATABASE_URL"),
            min_size=2,       # 최소 유지
            max_size=5,       # 최대 커넥션
            max_inactive_connection_lifetime=300,  # 5분 유휴 시 정리
        )
    return _pool

async def update_job_status(job_id: str, **kwargs):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ... UPDATE 실행
        pass
```

### PostgreSQL 서버 설정 (init.sql 또는 postgresql.conf)

```sql
-- 동시 커넥션 허용 수 (서비스 합계 + 여유)
ALTER SYSTEM SET max_connections = 100;

-- 공유 버퍼 (전체 RAM의 25% 권장, 24GB VM → 6GB)
ALTER SYSTEM SET shared_buffers = '256MB';    -- Docker 환경에서는 보수적으로

-- 작업 메모리 (정렬/해시 연산용, 커넥션당)
ALTER SYSTEM SET work_mem = '4MB';

-- 유지보수 작업 메모리
ALTER SYSTEM SET maintenance_work_mem = '64MB';
```

---

## 2. Kafka 파티션 + Worker 스케일링

### 파티션 전략

```
핵심 규칙:
- Worker 인스턴스 수 ≤ 파티션 수 (초과 Worker는 유휴 상태)
- 요청 토픽 (.requested) 파티션 = 예상 최대 Worker 수
- 완료/실패 토픽은 파티션 1~3이면 충분 (처리 속도가 빠름)
- DLQ (.failed) 토픽은 파티션 1 (발생 빈도 낮음)

파티션 수 결정:
- 소규모 (동시 job 10개 이하): 파티션 3
- 중규모 (동시 job 50개 이하): 파티션 6
- 대규모: Docker Compose 단일 VM 한계 → 인프라 재설계 필요
```

### topics.yml 표준

```yaml
# infra/kafka/topics.yml
topics:
  - name: reels.generation.requested
    partitions: 3                    # Worker 3개까지 병렬 처리 가능
    replication_factor: 1            # 단일 브로커이므로 1 고정
    config:
      retention.ms: 604800000        # 7일

  - name: reels.generation.completed
    partitions: 1                    # 완료 이벤트는 순서 불필요
    replication_factor: 1

  - name: reels.generation.failed
    partitions: 1                    # DLQ — 발생 빈도 낮음
    replication_factor: 1
    config:
      retention.ms: 2592000000       # 30일 (오래 보관)
```

### Worker 수평 확장 (docker-compose.yml)

```yaml
# docker-compose.yml
services:
  worker-reels:
    build:
      context: ./workers/reels
      dockerfile: Dockerfile
    deploy:
      replicas: 3                    # 파티션 수와 동일하게
      resources:
        limits:
          memory: 512M               # Worker당 메모리 제한
          cpus: "0.5"                # Worker당 CPU 제한
    environment:
      - KAFKA_GROUP_ID=reels-generation-workers   # 같은 group_id → 파티션 분배
```

```bash
# 런타임에 Worker 수 변경
docker compose up -d --scale worker-reels=5

# 단, 파티션 수 이하까지만 의미 있음
# 파티션 3인데 Worker 5 → 2개는 유휴
```

### Consumer Group 규칙

```
- 같은 역할의 Worker는 같은 group_id를 사용
- Kafka가 자동으로 파티션을 Worker들에게 분배
- Worker가 추가/제거되면 자동 리밸런싱

group_id 네이밍: {서비스}-{역할}-workers
  예: reels-generation-workers
      video-analysis-workers
```

---

## 3. MinIO 파일 처리 성능

### 업로드 규칙

```python
# 대용량 파일은 반드시 청크 스트리밍 (메모리에 전체 적재 금지)
CHUNK_SIZE = 1024 * 1024  # 1MB 청크

# ✅ 올바른 패턴
async with aiofiles.tempfile.NamedTemporaryFile() as tmp:
    while chunk := await upload_file.read(CHUNK_SIZE):
        await tmp.write(chunk)
    # tmp 파일을 MinIO에 업로드

# ❌ 금지 패턴
content = await upload_file.read()  # 전체 메모리 적재
```

### presigned URL 설정

```python
# presigned URL 만료 시간
PRESIGNED_EXPIRY = 3600       # 1시간 (기본)
# 사용자가 다운로드하기 전에 만료되지 않도록 충분히 설정
# 너무 길면 보안 위험 (24시간 이하 권장)
```

### MinIO 동시성

```
- MinIO 단일 인스턴스에서 동시 업로드 100+ 가능 (병목 아님)
- 병목은 네트워크 대역폭과 디스크 I/O
- 대용량 파일(100MB+)은 multipart upload 사용
  boto3의 upload_file()은 자동으로 multipart 처리
```

---

## 4. Redis 설정

```python
# backend/core/redis.py
import redis.asyncio as redis
from core.config import settings

redis_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=20,           # 최대 커넥션
    decode_responses=True,
)

redis_client = redis.Redis(connection_pool=redis_pool)
```

```
Redis 용도별 권장값:
- 캐시 전용: max_connections=20
- 세션 + 캐시: max_connections=30
- 큐(Celery 등) 겸용: max_connections=50
```

---

## 5. Docker 리소스 제한

### docker-compose.yml 리소스 설정

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"

  worker-reels:
    deploy:
      replicas: 3
      resources:
        limits:
          memory: 512M        # Worker당
          cpus: "0.5"         # Worker당

  crewai:
    deploy:
      resources:
        limits:
          memory: 1G          # LLM 응답 처리에 메모리 필요
          cpus: "1.0"

  postgres:
    deploy:
      resources:
        limits:
          memory: 2G
    command: >
      postgres
        -c max_connections=100
        -c shared_buffers=256MB
        -c work_mem=4MB

  kafka:
    deploy:
      resources:
        limits:
          memory: 1G

  redis:
    deploy:
      resources:
        limits:
          memory: 256M

  minio:
    deploy:
      resources:
        limits:
          memory: 512M

  grafana:
    deploy:
      resources:
        limits:
          memory: 256M
```

### OCI VM (4 OCPU, 24GB RAM) 기준 배분

```
서비스           메모리     CPU       인스턴스    합계 메모리
────────────────────────────────────────────────────────
Backend          512M      1.0       1          512M
Worker           512M      0.5       3          1.5G
CrewAI           1G        1.0       1          1G
PostgreSQL       2G        1.0       1          2G
Kafka            1G        0.5       1          1G
Redis            256M      0.25      1          256M
MinIO            512M      0.25      1          512M
Supabase         512M      0.5       1          512M
Loki             256M      0.25      1          256M
Promtail         128M      0.1       1          128M
Grafana          256M      0.25      1          256M
Nginx            64M       0.1       1          64M
────────────────────────────────────────────────────────
합계             ~8G       ~5.7                 ~8G

여유: 24GB - 8GB = 16GB (OS + 파일 캐시 + 버퍼)
```

---

## 6. 성능 모니터링 체크포인트

### Grafana에서 확인할 지표

```
■ 응답 시간:
  - API 엔드포인트별 p50/p95 응답 시간
  - 500ms 초과 요청 비율

■ 처리량:
  - Kafka Consumer lag (미처리 메시지 수)
  - 시간당 완료 job 수

■ 리소스:
  - DB 커넥션 사용량 (pool 고갈 여부)
  - 컨테이너별 메모리 사용량
  - 디스크 사용량 (MinIO, PostgreSQL)

■ 에러율:
  - DLQ 메시지 발생 빈도
  - HTTP 5xx 에러 비율
```

### 병목 판단 기준과 대응

```
증상                          원인 추정                 대응
─────────────────────────────────────────────────────────────────
API 응답 느림 (>500ms)        DB 커넥션 풀 고갈          pool_size 증가
Kafka lag 증가                Worker 처리 속도 부족      Worker replicas 증가
Worker OOM 종료               메모리 부족                리소스 제한 증가 또는 코드 최적화
DB CPU 100%                   인덱스 누락 또는 쿼리       EXPLAIN ANALYZE 후 인덱스 추가
MinIO 업로드 타임아웃          파일 크기 과다             청크 사이즈 조정, 크기 제한 강화
```

---

## 금지 패턴

```
❌ SQLAlchemy pool_size 미설정 (기본값 5로 병목)
❌ Worker에서 매번 asyncpg.connect() / close() (풀 사용 필수)
❌ Kafka 파티션 1개인데 Worker 여러 개 (유휴 인스턴스 발생)
❌ 파일 전체를 메모리에 읽어서 처리 (청크 스트리밍 필수)
❌ Docker 리소스 제한 없이 배포 (OOM Killer에 의한 예측 불가 종료)
❌ 성능 측정 없이 추측으로 튜닝 (Grafana 지표 확인 후 튜닝)
❌ Redis 커넥션 풀 미설정 (무제한 커넥션 생성)
```
