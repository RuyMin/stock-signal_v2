---
name: vibe-framework-test
description: >
  Vibe Framework의 테스트 전략과 도구 규칙.
  Architect가 TEST_SPEC.md를 설계하고, QA Engineer가 테스트 코드를 작성한다.
  구현 담당 엔지니어(Backend/AI/Mobile)는 테스트 코드를 작성하지 않는다.
  "테스트 코드 작성해줘", "테스트 실행해줘" 등의 요청 시 참조.
---

# Test Skill

## 핵심 원칙

1. **설계와 구현과 검증을 분리한다** — Architect가 사양을 쓰고, Engineer가 구현하고, QA가 검증한다
2. **테스트사양서가 먼저다** — 코드 구현 전에 TEST_SPEC.md가 존재해야 한다
3. **테스트는 사양서 기반이다** — QA Engineer는 TEST_SPEC.md에 정의된 케이스만 구현한다. 임의 추가 금지
4. **각 테스트는 독립 실행 가능하다** — 테스트 간 순서 의존성 금지
5. **실제 외부 API 호출 금지** — LLM, 외부 서비스는 반드시 mock 사용

---

## 테스트 계층 구조

```
┌─────────────────────────────────────────────┐
│  E2E 테스트 (사용자 시나리오 전체 플로우)       │  ← Docker 전체 기동 필요
├─────────────────────────────────────────────┤
│  통합 테스트 (모듈 간 연동)                    │  ← Docker 전체 기동 필요
├──────────┬──────────┬──────────┬────────────┤
│ API 단위  │ Worker   │ CrewAI   │ Mobile     │  ← 개별 실행 가능
│ (pytest)  │ (pytest) │ (pytest) │ (flutter)  │
└──────────┴──────────┴──────────┴────────────┘
```

---

## 테스트 도구 표준

### Python (Backend / CrewAI / Workers)

```
pytest              # 테스트 러너
pytest-asyncio      # 비동기 테스트
httpx               # FastAPI TestClient 대체 (async 지원)
pytest-cov          # 커버리지 측정
factory-boy         # 테스트 데이터 팩토리
```

### Flutter (Mobile)

```
flutter_test        # 위젯 테스트 (built-in)
mocktail            # mock 라이브러리 (mockito 대신 — codegen 불필요)
```

### 통합 / E2E

```
pytest              # Docker 기동 상태에서 실행
httpx               # 실제 HTTP 호출
asyncio             # Kafka/Realtime 이벤트 대기
```

---

## 디렉토리 구조

```
{project-root}/
├── tests/
│   ├── conftest.py              # 공통 fixture (DB, Kafka mock 등)
│   ├── factories/               # factory-boy 팩토리
│   │   ├── job_factory.py
│   │   └── event_factory.py
│   ├── unit/
│   │   ├── backend/             # API 엔드포인트 단위 테스트
│   │   │   ├── test_reels.py
│   │   │   └── test_jobs.py
│   │   ├── workers/             # Worker 처리 단위 테스트
│   │   │   └── test_{name}.py
│   │   └── crewai/              # Crew/Agent 단위 테스트
│   │       └── test_{crew}.py
│   ├── integration/             # 모듈 간 통합 테스트
│   │   ├── test_api_to_kafka.py
│   │   ├── test_worker_to_db.py
│   │   └── test_realtime.py
│   └── e2e/                     # E2E 시나리오 테스트
│       └── test_full_flow.py
│
├── mobile/
│   └── test/                    # Flutter 테스트 (Flutter 표준 위치)
│       ├── unit/
│       │   ├── providers/
│       │   └── services/
│       └── widget/
│           └── features/
```

---

## conftest.py 표준 fixture

```python
# tests/conftest.py

import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.core.database import get_db
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

@pytest.fixture
async def client():
    """FastAPI 테스트 클라이언트."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

@pytest.fixture
async def db_session():
    """테스트용 DB 세션 (각 테스트마다 롤백)."""
    # 테스트 DB에 연결, 트랜잭션 시작
    # yield session
    # 테스트 후 롤백 — 테스트 간 격리 보장

@pytest.fixture
def mock_kafka_producer(mocker):
    """Kafka Producer mock — 실제 발행 없이 호출 검증."""
    return mocker.patch("backend.core.kafka.producer.send_and_wait")

@pytest.fixture
def mock_minio(mocker):
    """MinIO mock — 실제 업로드 없이 object_key 반환."""
    return mocker.patch("backend.core.minio.upload_file",
                        return_value="raw-uploads/test-job/video.mp4")

@pytest.fixture
def sample_job_id():
    """테스트용 고정 job_id."""
    return "test-job-00000000"
```

---

## 단위 테스트 작성 패턴

### API 엔드포인트 테스트

```python
# tests/unit/backend/test_reels.py
# TEST_SPEC.md 기반으로 작성

import pytest
from httpx import AsyncClient

class TestReelsGenerate:
    """POST /reels/generate — TEST_SPEC API-001 ~ API-E004"""

    @pytest.mark.asyncio
    async def test_api_001_valid_upload(self, client, mock_kafka_producer, mock_minio):
        """API-001: 유효한 영상 업로드 → job_id 반환."""
        response = await client.post("/reels/generate", files={"video": ...})
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"
        mock_kafka_producer.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_e001_missing_video(self, client):
        """API-E001: 영상 없이 요청 → MISSING_FIELD."""
        response = await client.post("/reels/generate")
        assert response.status_code == 400
        assert response.json()["error_code"] == "MISSING_FIELD"

    @pytest.mark.asyncio
    async def test_api_h001_request_id_auto_generated(self, client, mock_kafka_producer, mock_minio):
        """API-H001: X-Request-ID 없이 요청 → 서버가 자동 생성."""
        response = await client.post("/reels/generate", files={"video": ...})
        assert "x-request-id" in response.headers
```

### Worker 테스트

```python
# tests/unit/workers/test_video_processor.py

class TestVideoProcessor:
    """Worker 처리 — TEST_SPEC WRK-001 ~ WRK-E003"""

    @pytest.mark.asyncio
    async def test_wrk_001_normal_processing(self, db_session, mock_minio):
        """WRK-001: 정상 메시지 처리 → completed."""
        message = {"job_id": "test-001", "video_key": "raw/video.mp4"}
        await process(message)
        job = await db_session.get(Job, "test-001")
        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_wrk_e001_file_not_found(self, db_session):
        """WRK-E001: MinIO 파일 없음 → failed + DLQ."""
        message = {"job_id": "test-002", "video_key": "nonexistent.mp4"}
        await process(message)
        job = await db_session.get(Job, "test-002")
        assert job.status == "failed"
```

### CrewAI 테스트

```python
# tests/unit/crewai/test_reels_crew.py

class TestReelsCrew:
    """CrewAI Crew — TEST_SPEC CREW-001 ~ CREW-E002"""

    @pytest.mark.asyncio
    async def test_crew_001_normal_execution(self, mock_llm, mock_minio):
        """CREW-001: 정상 Crew 실행 → 모든 Task 완료."""
        # mock_llm은 미리 정의된 응답을 반환
        crew = ReelsGenerationCrew(job_id="test-001", inputs={...})
        result = await crew.kickoff()
        assert result is not None
        # 각 Agent step 로그 존재 검증
```

---

## Flutter 테스트 패턴

### Provider 단위 테스트

```dart
// mobile/test/unit/providers/test_job_provider.dart

void main() {
  group('JobProvider — TEST_SPEC MOB-001 ~ MOB-004', () {
    test('MOB-001: Job 제출 후 queued 상태', () async {
      final container = ProviderContainer(overrides: [
        apiServiceProvider.overrideWithValue(MockApiService()),
      ]);
      // 검증
    });
  });
}
```

---

## 통합 테스트 패턴

```python
# tests/integration/test_api_to_kafka.py
# Docker 전체 기동 상태에서 실행

class TestApiToKafka:
    """INT-002: API → Kafka → Worker 연동."""

    @pytest.mark.asyncio
    async def test_int_002_kafka_message_delivery(self):
        """POST 요청 → Kafka 메시지 발행 → Worker 수신 확인."""
        async with AsyncClient(base_url="http://localhost:8000") as client:
            response = await client.post("/reels/generate", files={...})
            job_id = response.json()["job_id"]

        # Kafka Consumer로 메시지 수신 확인
        # DB에서 job status 변화 확인 (polling)
        # Grafana 로그에서 job_id 존재 확인
```

---

## 테스트 실행 명령어

```bash
# 단위 테스트 (Docker 불필요)
pytest tests/unit/ -v --cov=backend --cov=workers --cov=crewai

# 통합 테스트 (Docker 기동 필요)
docker compose up -d
pytest tests/integration/ -v

# E2E 테스트 (Docker 기동 필요)
docker compose up -d
pytest tests/e2e/ -v

# Flutter 단위 테스트
cd mobile && flutter test test/unit/

# Flutter 위젯 테스트
cd mobile && flutter test test/widget/

# 전체 실행
pytest tests/ -v --cov
```

---

## 테스트 데이터 규칙

```
- 테스트 DB는 매 테스트마다 초기화 (트랜잭션 롤백 또는 truncate)
- 테스트 파일은 tests/fixtures/ 에 저장 (최소 크기)
- 테스트 환경변수는 .env.test 파일 사용
- LLM API 키는 테스트에서 절대 사용 금지 — mock만 사용
- MinIO 테스트 버킷: test-raw, test-processed (실제 버킷과 분리)
```

---

## 금지 패턴

```
❌ 구현 담당 엔지니어(Backend/AI/Mobile)가 테스트 코드 작성
❌ TEST_SPEC.md에 없는 테스트 케이스 임의 추가
❌ 테스트에서 실제 LLM API 호출
❌ 테스트 간 순서 의존성 (test_A 실행 후에만 test_B 가능)
❌ 테스트 DB와 개발 DB 공유
❌ print()로 테스트 결과 확인 (assert만 사용)
❌ 하드코딩된 job_id / 파일 경로 (fixture/factory 사용)
❌ 테스트 실패 시 "일단 skip" 처리 후 방치
```
