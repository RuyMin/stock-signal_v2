---
name: vibe-framework-docker
description: >
  Vibe Framework의 Docker Compose 구성 규칙.
  docker-compose.yml 작성, 새 서비스 추가, 컨테이너 설정 시 반드시 참조.
---

# Docker Skill

## 서비스 구성 전체 목록

```yaml
services:
  # ── 인프라 ──────────────────────────────
  postgres        # 메인 DB
  redis           # 캐시 / 세션
  zookeeper       # Kafka 의존성
  kafka           # 메시지 큐
  minio           # 파일 스토리지
  supabase        # Realtime 푸시
  
  # ── 로그 수집 ────────────────────────────
  loki            # 로그 저장 DB
  promtail        # 로그 수집 에이전트
  grafana         # 로그 시각화 + 대시보드
  
  # ── 애플리케이션 ──────────────────────────
  backend         # FastAPI 게이트웨이
  crewai          # CrewAI 오케스트레이션
  
  # ── Workers (기능별 독립 컨테이너) ──────────
  worker-{name}   # 예: worker-video-analyzer, worker-script-writer
```

---

## 표준 docker-compose.yml 템플릿

```yaml
version: '3.9'

networks:
  vibe-net:
    driver: bridge

volumes:
  postgres-data:
  redis-data:
  minio-data:
  kafka-data:

services:

  # ── PostgreSQL ──────────────────────────
  postgres:
    image: postgres:16-alpine
    container_name: vibe-postgres
    networks: [vibe-net]
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ── Redis ───────────────────────────────
  redis:
    image: redis:7-alpine
    container_name: vibe-redis
    networks: [vibe-net]
    volumes:
      - redis-data:/data
      - ./infra/redis/redis.conf:/usr/local/etc/redis/redis.conf
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ── Zookeeper ───────────────────────────
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    container_name: vibe-zookeeper
    networks: [vibe-net]
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    volumes:
      - kafka-data:/var/lib/zookeeper

  # ── Kafka ───────────────────────────────
  kafka:
    image: confluentinc/cp-kafka:7.6.0
    container_name: vibe-kafka
    networks: [vibe-net]
    depends_on: [zookeeper]
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
    ports:
      - "9092:9092"
    healthcheck:
      test: ["CMD", "kafka-broker-api-versions", "--bootstrap-server", "kafka:9092"]
      interval: 30s
      timeout: 10s
      retries: 5

  # ── MinIO ───────────────────────────────
  minio:
    image: minio/minio:latest
    container_name: vibe-minio
    networks: [vibe-net]
    volumes:
      - minio-data:/data
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 10s
      retries: 5

  # ── FastAPI Backend ─────────────────────
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: vibe-backend
    networks: [vibe-net]
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
      kafka: {condition: service_healthy}
    environment:
      - DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      - REDIS_URL=redis://redis:6379
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
      - MINIO_ENDPOINT=minio:9000
    volumes:
      - ./shared/schemas:/app/schemas  # 공유 스키마 마운트
    ports:
      - "8000:8000"
    # entrypoint.sh가 alembic upgrade head 실행 후 uvicorn 시작
    # 새 환경: init.sql 실행 후 alembic stamp head (최신 표시)
    # 기존 환경: alembic upgrade head (차이분 적용)

  # ── CrewAI ──────────────────────────────
  crewai:
    build:
      context: ./crewai
      dockerfile: Dockerfile
    container_name: vibe-crewai
    networks: [vibe-net]
    depends_on:
      kafka: {condition: service_healthy}
      postgres: {condition: service_healthy}
    environment:
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
      - DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      - REDIS_URL=redis://redis:6379
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - MINIO_ENDPOINT=minio:9000

  # ── Worker 템플릿 (복사해서 추가) ───────────
  # worker-{name}:
  #   build:
  #     context: ./workers/{name}
  #     dockerfile: Dockerfile
  #   networks: [vibe-net]
  #   depends_on:
  #     kafka: {condition: service_healthy}
  #   deploy:
  #     replicas: 3               # Kafka 파티션 수와 동일하게 (PERFORMANCE_SKILL 참조)
  #     resources:
  #       limits:
  #         memory: 512M
  #         cpus: "0.5"
  #   environment:
  #     - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
  #     - KAFKA_GROUP_ID={name}-workers
  #     - KAFKA_TOPIC_IN={name}.process.requested
  #     - KAFKA_TOPIC_OUT={name}.process.completed
  #     - MINIO_ENDPOINT=minio:9000
```

> Worker 스케일링, 리소스 배분 상세 → `infra/PERFORMANCE_SKILL.md` 참조.

---

## Worker 추가 규칙

새 Worker 컨테이너 추가 시 반드시 아래를 정의한다.

```yaml
# 추가 체크리스트
- 서비스명: worker-{기능명} (kebab-case)
- Kafka 입력 토픽: {기능명}.process.requested
- Kafka 출력 토픽: {기능명}.process.completed
- Kafka 에러 토픽: {기능명}.process.failed
- 공유 볼륨: 없음 (MinIO를 통해서만 파일 공유)
- 네트워크: vibe-net 필수
```

---

## 환경변수 .env.example 표준

```env
# PostgreSQL
POSTGRES_DB=vibedb
POSTGRES_USER=vibeuser
POSTGRES_PASSWORD=changeme

# Redis
REDIS_PASSWORD=changeme

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# MinIO
MINIO_ROOT_USER=vibeadmin
MINIO_ROOT_PASSWORD=changeme
MINIO_BUCKET_RAW=raw-uploads
MINIO_BUCKET_OUTPUT=processed-outputs

# AI
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Supabase
SUPABASE_URL=http://supabase:8000
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...
```

---

## healthcheck 의존성 원칙

```
인프라 서비스 (postgres, redis, kafka, minio)
    ↓ healthcheck 통과 후
애플리케이션 서비스 (backend, crewai)
    ↓ 준비 완료 후
Worker 서비스들
```

Worker는 Kafka가 healthy 상태여야만 시작한다.
Backend는 postgres + redis + kafka 모두 healthy여야 시작한다.

---

## 환경 분리 (dev / staging / prod)

### 3환경 구조

```
환경       용도                   실행 위치      Docker Compose 조합
────────────────────────────────────────────────────────────────────
dev        로컬 개발/디버깅        개발자 PC     docker-compose.yml + docker-compose.dev.yml
staging    통합 테스트/QA 검증     OCI VM        docker-compose.yml + docker-compose.staging.yml
prod       운영                   OCI VM        docker-compose.yml + docker-compose.prod.yml
```

**docker-compose.yml은 공통 베이스.** 환경별 차이는 오버라이드 파일로만 정의한다.

### 환경별 차이 정리

```
항목                dev                      staging                  prod
───────────────────────────────────────────────────────────────────────────
이미지 소스         로컬 build               ghcr.io (latest)         ghcr.io (SHA 태그)
핫리로드            ✅ (볼륨 마운트)           ❌                       ❌
로그 레벨           DEBUG                    INFO                     WARNING
Grafana 접근        localhost:3000           localhost:3000           차단 (SSH 터널만)
restart 정책        no (기본)                always                   always
데이터 볼륨         임시 (컨테이너 삭제 시)    영속 (named volume)      영속 (named volume)
Supabase           self-hosted (로컬)        self-hosted (VM)         self-hosted (VM)
MinIO 버킷          dev-raw, dev-processed   stg-raw, stg-processed   raw, processed
DB 이름             vibe_dev                 vibe_staging             vibe_prod
CORS                *                        도메인 한정               도메인 한정
```

### .env 파일 환경별 관리

```
.env.example      Git에 커밋. 모든 변수의 플레이스홀더.
.env.dev          로컬 개발용 (각 개발자 로컬에만 존재)
.env.staging      staging VM의 /opt/vibe/ 에만 존재
.env.prod         prod VM의 /opt/vibe/ 에만 존재

.env.dev / .env.staging / .env.prod 는 절대 Git에 포함 안 됨.
```

### .env.example 표준

```env
# === 환경 식별 ===
VIBE_ENV=dev                              # dev | staging | prod

# === PostgreSQL ===
POSTGRES_USER=vibe
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_DB=vibe_dev                      # vibe_dev | vibe_staging | vibe_prod

# === Backend ===
DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
BACKEND_LOG_LEVEL=DEBUG                   # DEBUG | INFO | WARNING
CORS_ORIGINS=*                            # * (dev) | https://staging.example.com | https://example.com

# === Kafka ===
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# === MinIO ===
MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=CHANGE_ME
MINIO_BUCKET_PREFIX=dev                   # dev | stg | (빈 문자열=prod)

# === Redis ===
REDIS_URL=redis://redis:6379

# === Supabase ===
SUPABASE_URL=http://supabase:8000
SUPABASE_ANON_KEY=CHANGE_ME
SUPABASE_SERVICE_KEY=CHANGE_ME
SUPABASE_JWT_SECRET=CHANGE_ME

# === Grafana ===
GRAFANA_ADMIN_PASSWORD=CHANGE_ME
```

### docker-compose.dev.yml (개발)

```yaml
# 개발환경에서만 적용되는 설정
# 사용: docker compose -f docker-compose.yml -f docker-compose.dev.yml up
services:
  backend:
    volumes:
      - ./backend:/app                     # 핫리로드용 볼륨 마운트
    command: uvicorn main:app --reload --host 0.0.0.0 --port 8000
    environment:
      - VIBE_ENV=dev
      - BACKEND_LOG_LEVEL=DEBUG
      - CORS_ORIGINS=*

  crewai:
    volumes:
      - ./crewai:/app
    environment:
      - VIBE_ENV=dev
```

### docker-compose.staging.yml (스테이징)

```yaml
# 스테이징 환경 설정
# 사용: docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d
services:
  backend:
    image: ghcr.io/{owner}/{repo}/backend:latest
    build: !reset null
    restart: always
    environment:
      - VIBE_ENV=staging
      - BACKEND_LOG_LEVEL=INFO
      - CORS_ORIGINS=https://staging.{domain}

  crewai:
    image: ghcr.io/{owner}/{repo}/crewai:latest
    build: !reset null
    restart: always
    environment:
      - VIBE_ENV=staging

  postgres:
    restart: always
    volumes:
      - postgres_staging_data:/var/lib/postgresql/data

  minio:
    restart: always
    volumes:
      - minio_staging_data:/data

volumes:
  postgres_staging_data:
  minio_staging_data:
```

### docker-compose.prod.yml (운영)

> DEPLOY_SKILL.md에 상세 정의됨. 여기서는 구조만 기재.

```yaml
# 운영 환경 설정
# 사용: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
services:
  backend:
    image: ghcr.io/{owner}/{repo}/backend:{SHA}
    build: !reset null
    restart: always
    environment:
      - VIBE_ENV=prod
      - BACKEND_LOG_LEVEL=WARNING
      - CORS_ORIGINS=https://{domain}
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  grafana:
    ports: !reset []                        # 외부 접근 차단

  postgres:
    restart: always
    volumes:
      - postgres_prod_data:/var/lib/postgresql/data

  minio:
    restart: always
    volumes:
      - minio_prod_data:/data

volumes:
  postgres_prod_data:
  minio_prod_data:
```

### 실행 명령어 정리

```bash
# 개발 (로컬)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# 스테이징 (OCI VM)
docker compose -f docker-compose.yml -f docker-compose.staging.yml --env-file .env.staging up -d

# 운영 (OCI VM)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d
```

---

## 서비스 포트 표준

```
FastAPI Backend  : 8000
PostgreSQL       : 5432
Redis            : 6379
Kafka            : 9092
MinIO API        : 9000
MinIO Console    : 9001
Supabase         : 8001
Loki             : 3100
Grafana          : 3000  ← 로그 조회 메인 UI
```

---

## 로그 수집 서비스 (항상 포함)

Loki + Promtail + Grafana는 모든 프로젝트에 기본 포함한다.
개발 중 로그 추적이 핵심이므로 선택이 아닌 필수.
상세 설정 → `infra/LOGGING_SKILL.md`

```yaml
# docker-compose.yml에 항상 추가
volumes:
  loki-data:
  grafana-data:

services:
  loki:
    image: grafana/loki:2.9.0
    container_name: vibe-loki
    networks: [vibe-net]
    volumes:
      - loki-data:/loki
      - ./infra/loki/loki-config.yml:/etc/loki/local-config.yaml
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/local-config.yaml
    healthcheck:
      test: ["CMD-SHELL", "wget -q --tries=1 -O- http://localhost:3100/ready"]
      interval: 10s
      timeout: 5s
      retries: 5

  promtail:
    image: grafana/promtail:2.9.0
    container_name: vibe-promtail
    networks: [vibe-net]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - ./infra/promtail/promtail-config.yml:/etc/promtail/config.yml
    command: -config.file=/etc/promtail/config.yml
    depends_on:
      loki: {condition: service_healthy}

  grafana:
    image: grafana/grafana:10.4.0
    container_name: vibe-grafana
    networks: [vibe-net]
    volumes:
      - grafana-data:/var/lib/grafana
      - ./infra/grafana/provisioning:/etc/grafana/provisioning
      - ./infra/grafana/dashboards:/var/lib/grafana/dashboards
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "3000:3000"
    depends_on:
      loki: {condition: service_healthy}
```
