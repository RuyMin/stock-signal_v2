---
name: vibe-framework-logging
description: >
  Vibe Framework의 로그 전략 전체 정의.
  모든 컨테이너의 로그 출력 형식, Correlation ID 사용법,
  Loki/Grafana 구성 규칙 정의.
  모든 페르소나가 코드 작성 시 반드시 참조.
  이 규칙을 벗어나는 로그 코드는 생성하지 말 것.
---

# Logging Skill

## 핵심 원칙

1. **모든 로그는 JSON 구조화 형식**으로 출력한다 — `print()` 절대 금지
2. **모든 로그에 `job_id`를 포함**한다 — Correlation ID 추적의 핵심
3. **로그 레벨을 반드시 구분**한다 — DEBUG / INFO / WARNING / ERROR / CRITICAL
4. **중간 처리 단계마다 INFO 로그**를 남긴다 — 어느 단계에서 멈췄는지 파악 가능하게
5. **에러는 반드시 stack trace 포함**한다 — 에러 메시지만으로는 부족

---

## 로그 수집 스택

| 역할 | 기술 |
|------|------|
| 로그 저장 DB | Loki |
| 로그 수집 에이전트 | Promtail |
| 시각화 / 조회 | Grafana |
| 로그 출력 형식 | JSON (structlog) |

---

## 전체 로그 아키텍처

```
[backend] [crewai] [worker-*]
    │ stdout (JSON)
    ▼
Promtail
    │ 라벨 부착 (service, job_id, level)
    ▼
Loki (로그 저장)
    │
    ▼
Grafana
    ├── 서비스별 실시간 로그
    ├── job_id 기반 전체 흐름 추적
    ├── 에러 대시보드
    └── 알림 (에러율 임계치 초과 시)
```

---

## Python 로그 표준 (backend / crewai / workers)

### structlog 설정 (모든 Python 서비스 공통)

```python
# core/logging.py — 모든 Python 서비스에 포함
import structlog
import logging
import sys

def setup_logging(service_name: str):
    """
    모든 Python 컨테이너의 진입점에서 반드시 호출.
    main.py 또는 앱 시작 시 1회 실행.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,     # job_id 등 컨텍스트 자동 포함
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),    # stack trace 자동 포함
            structlog.processors.JSONRenderer(),         # JSON 출력
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
    # 서비스명을 전역 컨텍스트에 바인딩
    structlog.contextvars.bind_contextvars(service=service_name)

# 로거 인스턴스 생성
logger = structlog.get_logger()
```

### job_id Correlation ID 바인딩

```python
# 모든 작업 시작 시 job_id를 컨텍스트에 바인딩
import structlog

async def process_job(job_id: str, payload: dict):
    # 이 시점부터 이 코루틴의 모든 로그에 job_id 자동 포함
    structlog.contextvars.bind_contextvars(job_id=job_id)

    logger.info("job_started", payload_keys=list(payload.keys()))
    # 출력: {"job_id": "abc-123", "service": "backend", "level": "info",
    #         "event": "job_started", "payload_keys": ["video_key", "guideline_id"]}

    try:
        result = await do_work(payload)
        logger.info("job_completed", result_url=result["url"])
    except Exception as e:
        logger.error("job_failed", error=str(e), exc_info=True)
        raise
    finally:
        structlog.contextvars.unbind_contextvars("job_id")
```

### 단계별 로그 패턴

```python
# CrewAI Agent 단계별 로그 — BaseAgent에서 자동 처리
logger.info("agent_step_start",
    agent=self.role,
    step=step_number,
    task=task_name,
)

logger.info("agent_step_complete",
    agent=self.role,
    step=step_number,
    duration_ms=elapsed,
    output_preview=output[:100],
)

logger.warning("agent_retry",
    agent=self.role,
    attempt=retry_count,
    reason=str(e),
)

logger.error("agent_failed",
    agent=self.role,
    step=step_number,
    error=str(e),
    exc_info=True,     # stack trace 자동 포함
)
```

### Worker 단계별 로그 패턴

```python
# workers/{name}/processor.py
async def process(event: dict):
    job_id = event["job_id"]
    structlog.contextvars.bind_contextvars(job_id=job_id)

    logger.info("worker_received", topic=TOPIC_IN)

    # 단계마다 로그
    logger.info("step_download_start", object_key=event["video_key"])
    await download_from_minio(event["video_key"])
    logger.info("step_download_complete", duration_ms=elapsed)

    logger.info("step_process_start", tool="ffmpeg")
    result = await run_ffmpeg(...)
    logger.info("step_process_complete", output_path=result, duration_ms=elapsed)

    logger.info("step_upload_start", bucket="processed-outputs")
    url = await upload_to_minio(result)
    logger.info("step_upload_complete", result_url=url)

    logger.info("worker_completed", total_duration_ms=total_elapsed)
```

---

## 로그 레벨 사용 기준

| 레벨 | 사용 상황 | 예시 |
|------|----------|------|
| DEBUG | 개발 중 상세 추적 | 함수 파라미터, 중간 계산값 |
| INFO | 정상 처리 흐름의 모든 단계 | job_started, step_complete, job_finished |
| WARNING | 비정상이지만 처리 가능 | 재시도 발생, 캐시 미스, 느린 응답 |
| ERROR | 처리 실패, 복구 가능 | API 호출 실패, 파일 없음 |
| CRITICAL | 서비스 중단 수준 | DB 연결 불가, Kafka 연결 불가 |

---

## 필수 로그 이벤트 목록

모든 서비스가 반드시 이 이벤트들을 로깅해야 한다.

### FastAPI (backend)
```python
job_queued          # Kafka 발행 완료
job_status_queried  # 상태 조회 요청
upload_complete     # MinIO 업로드 완료
request_error       # 요청 검증 실패
```

### CrewAI
```python
crew_started        # Crew.kickoff() 시작
agent_step_start    # Agent 각 스텝 시작
agent_step_complete # Agent 각 스텝 완료
agent_retry         # Agent 재시도
crew_completed      # 전체 완료
crew_failed         # 전체 실패
dlq_published       # DLQ 발행
```

### Workers
```python
worker_received     # Kafka 메시지 수신
step_{name}_start   # 각 처리 단계 시작
step_{name}_complete # 각 처리 단계 완료
worker_completed    # 처리 완료
worker_failed       # 처리 실패
retry_scheduled     # 재시도 예약
dlq_published       # DLQ 발행
```

---

## Grafana 대시보드 구성

### 대시보드 1: Job 전체 흐름 추적

```
검색: job_id = "abc-123"
표시:
  타임라인으로 각 서비스별 로그
  backend → crewai → worker 순서로 정렬
  각 단계 소요 시간
```

### 대시보드 2: 서비스별 실시간 로그

```
각 서비스별 로그 스트림
에러/경고 하이라이트
```

### 대시보드 3: 에러 현황

```
최근 1시간 에러 건수
서비스별 에러율
반복 에러 패턴
```

### 대시보드 4: 성능 모니터링

```
job 처리 시간 분포
단계별 평균 소요 시간
Worker 처리량 (jobs/hour)
```

---

## Loki / Promtail / Grafana Docker 구성

```yaml
# docker-compose.yml에 추가할 서비스들

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
      - /var/run/docker.sock:/var/run/docker.sock:ro  # Docker 로그 접근
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

---

## infra/promtail/promtail-config.yml

```yaml
server:
  http_listen_port: 9080

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: vibe-containers
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      # 컨테이너명을 service 라벨로
      - source_labels: [__meta_docker_container_name]
        regex: '/vibe-(.*)'
        target_label: service
        replacement: '$1'
      # 로그 레벨 추출 (JSON 로그의 level 필드)
      - source_labels: [__meta_docker_container_name]
        target_label: job
        replacement: vibe-framework
    pipeline_stages:
      # JSON 파싱
      - json:
          expressions:
            level: level
            job_id: job_id
            event: event
      # 추출한 값을 라벨로
      - labels:
          level:
          job_id:
          event:
```

---

## infra/loki/loki-config.yml

```yaml
auth_enabled: false

server:
  http_listen_port: 3100

ingester:
  lifecycler:
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1

schema_config:
  configs:
    - from: 2024-01-01
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h

storage_config:
  boltdb_shipper:
    active_index_directory: /loki/index
    cache_location: /loki/cache
  filesystem:
    directory: /loki/chunks

limits_config:
  retention_period: 168h   # 7일 보관
```

---

## requirements.txt 추가 항목 (Python 서비스 공통)

```
structlog==24.1.0
```

---

## 금지 패턴

```python
# ❌ print() 사용
print(f"Job {job_id} started")

# ❌ 비구조화 로그
logging.info("Job started")

# ❌ job_id 없는 로그
logger.info("processing_complete", result=result)

# ❌ 에러를 WARNING으로 낮춰서 기록
logger.warning("job_failed", error=str(e))  # 실패는 ERROR

# ✅ 올바른 패턴
structlog.contextvars.bind_contextvars(job_id=job_id)
logger.info("processing_complete", result_url=result["url"], duration_ms=elapsed)
logger.error("job_failed", error=str(e), exc_info=True)
```
