---
name: vibe-framework-api-contract
description: >
  Vibe Framework의 Flutter ↔ FastAPI 간 API 계약 정의.
  HTTP 헤더 규칙, 에러 응답 구조, 공통 응답 형식 정의.
  Backend Engineer와 Mobile Engineer 모두 반드시 읽어야 함.
  이 파일의 규칙을 벗어나는 헤더/에러 구조 코드는 절대 생성 금지.
---

# API Contract Skill

## 핵심 원칙

1. **모든 요청에 X-Request-ID 포함** — 요청 단위 추적의 기본
2. **job_id가 있는 요청은 X-Job-ID 포함** — Grafana 로그와 연결
3. **에러 응답 구조는 항상 동일** — Flutter 에러 파싱 코드 일원화
4. **에러 코드는 snake_case 대문자** — 코드로 분기 처리 가능하게
5. **응답에 X-Request-ID 에코** — 요청/응답 매칭 가능하게

---

## 공통 요청 헤더

모든 API 요청에 아래 헤더를 포함한다.

```
Authorization: Bearer {access_token}     # 인증 (비로그인 엔드포인트 제외)
Content-Type: application/json           # 파일 업로드는 multipart/form-data
Accept: application/json
X-Request-ID: {uuid_v4}                  # 요청 단위 고유 ID (Flutter에서 생성)
X-App-Version: {semver}                  # 예: 1.0.0
```

job_id가 있는 요청 (작업 제출 후 조회 등):
```
X-Job-ID: {job_id}                       # Grafana 로그 추적용
```

---

## 공통 응답 헤더

모든 API 응답에 아래 헤더를 포함한다.

```
Content-Type: application/json
X-Request-ID: {echo_of_request_id}       # 요청 헤더의 X-Request-ID 그대로 반환
X-Response-Time: {milliseconds}          # 처리 시간 (ms)
```

---

## 에러 응답 구조

**모든 에러는 반드시 이 구조로 반환한다.**
HTTP 상태코드와 무관하게 body 구조는 동일하다.

```json
{
  "error_code": "VIDEO_TOO_LARGE",
  "message": "파일 크기가 제한을 초과했습니다 (최대 500MB)",
  "job_id": "abc-123",
  "request_id": "req-uuid-456",
  "timestamp": "2024-01-01T00:00:00Z",
  "detail": {}
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| error_code | string | ✅ | 에러 식별 코드 (Flutter 분기 처리용) |
| message | string | ✅ | 사용자에게 표시할 메시지 (한국어) |
| job_id | string | 조건부 | job 관련 에러일 때만 포함 |
| request_id | string | ✅ | X-Request-ID 에코 |
| timestamp | ISO8601 | ✅ | 에러 발생 시각 |
| detail | object | ✅ | 추가 정보 (없으면 빈 객체 `{}`) |

---

## 에러 코드 표준 목록

### 인증/인가
```
UNAUTHORIZED          # 401 — 토큰 없음 또는 만료
FORBIDDEN             # 403 — 권한 없음
TOKEN_EXPIRED         # 401 — 토큰 만료 (갱신 필요)
```

### 요청 유효성
```
INVALID_REQUEST       # 400 — 요청 형식 오류
MISSING_FIELD         # 400 — 필수 필드 누락
INVALID_FILE_TYPE     # 400 — 허용되지 않는 파일 형식
VIDEO_TOO_LARGE       # 400 — 파일 크기 초과
GUIDELINE_NOT_FOUND   # 404 — 가이드라인 없음
```

### Job 관련
```
JOB_NOT_FOUND         # 404 — job_id 없음
JOB_ALREADY_RUNNING   # 409 — 이미 처리 중
JOB_FAILED            # 422 — 처리 실패
JOB_EXPIRED           # 410 — 결과 만료 (presigned URL 등)
```

### 도메인 — 보유 종목 (stock-signal 도입)
```
HOLDING_NOT_FOUND     # 404 — 등록된 보유 종목 아님
```

### 서버
```
INTERNAL_ERROR        # 500 — 서버 내부 오류
SERVICE_UNAVAILABLE   # 503 — 의존 서비스 불가 (Kafka, MinIO 등)
```

> 새 에러 코드 추가 시 이 목록에 먼저 정의하고 구현한다.
> 임의로 새 에러 코드를 생성하지 않는다.

---

## 성공 응답 구조

### Job 제출 응답 (즉시 반환)

```json
{
  "job_id": "abc-123",
  "status": "queued",
  "message": "작업이 대기열에 추가되었습니다",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### Job 상태 조회 응답

```json
{
  "job_id": "abc-123",
  "status": "processing",
  "progress": 45,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:01:30Z",
  "result_url": null,
  "error_message": null
}
```

### Job 결과 조회 응답 (completed)

```json
{
  "job_id": "abc-123",
  "status": "completed",
  "progress": 100,
  "download_url": "https://minio:9000/...",
  "expires_in": 3600,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:05:00Z"
}
```

---

## Pydantic 스키마 표준 (FastAPI)

```python
# shared/schemas/common.py
from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime
from enum import Enum
import uuid

class JobStatusEnum(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class ErrorResponse(BaseModel):
    """모든 에러 응답의 표준 구조. 반드시 이 모델 사용."""
    error_code: str
    message: str
    job_id: Optional[str] = None
    request_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    detail: dict[str, Any] = {}

class JobQueuedResponse(BaseModel):
    """Job 제출 즉시 응답."""
    job_id: str
    status: JobStatusEnum = JobStatusEnum.QUEUED
    message: str = "작업이 대기열에 추가되었습니다"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class JobStatusResponse(BaseModel):
    """Job 상태 조회 응답."""
    job_id: str
    status: JobStatusEnum
    progress: int = Field(ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    result_url: Optional[str] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True

class JobResultResponse(BaseModel):
    """Job 완료 결과 응답 (presigned URL 포함)."""
    job_id: str
    status: JobStatusEnum
    progress: int = 100
    download_url: str
    expires_in: int = 3600
    created_at: datetime
    updated_at: datetime
```

---

## FastAPI 헤더 처리 표준

```python
# backend/core/dependencies.py
from fastapi import Request, Response
import uuid
import time

async def add_response_headers(request: Request, call_next):
    """
    미들웨어 — 모든 응답에 공통 헤더 자동 추가.
    main.py에 반드시 등록.
    """
    start_time = time.time()
    response = await call_next(request)

    # X-Request-ID 에코
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response.headers["X-Request-ID"] = request_id

    # 응답 시간
    elapsed_ms = int((time.time() - start_time) * 1000)
    response.headers["X-Response-Time"] = str(elapsed_ms)

    return response


def get_request_id(request: Request) -> str:
    """라우터에서 request_id 추출용 Depends."""
    return request.headers.get("X-Request-ID", str(uuid.uuid4()))

def get_job_id_header(request: Request) -> Optional[str]:
    """라우터에서 job_id 헤더 추출용 Depends."""
    return request.headers.get("X-Job-ID")
```

```python
# backend/main.py
from core.dependencies import add_response_headers

app = FastAPI()
app.middleware("http")(add_response_headers)  # 반드시 등록
```

### 에러 응답 생성 표준

```python
# backend/core/exceptions.py
from fastapi import Request
from fastapi.responses import JSONResponse
from schemas.common import ErrorResponse
import structlog

logger = structlog.get_logger()

class VibeException(Exception):
    """모든 커스텀 예외의 부모 클래스."""
    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = 400,
        job_id: str = None,
        detail: dict = None,
    ):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.job_id = job_id
        self.detail = detail or {}

async def vibe_exception_handler(request: Request, exc: VibeException):
    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.error("api_error",
        error_code=exc.error_code,
        message=exc.message,
        job_id=exc.job_id,
        request_id=request_id,
        status_code=exc.status_code,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error_code=exc.error_code,
            message=exc.message,
            job_id=exc.job_id,
            request_id=request_id,
            detail=exc.detail,
        ).model_dump(mode="json"),
    )

# 사용 예시
raise VibeException(
    error_code="VIDEO_TOO_LARGE",
    message="파일 크기가 제한을 초과했습니다 (최대 500MB)",
    status_code=400,
    detail={"max_size_mb": 500, "actual_size_mb": 750},
)
```

---

## Dart 모델 표준 (Flutter)

```dart
// shared/models/api_models.dart
import 'package:freezed_annotation/freezed_annotation.dart';

part 'api_models.freezed.dart';
part 'api_models.g.dart';

/// 모든 에러 응답의 표준 구조
@freezed
class ErrorResponse with _$ErrorResponse {
  const factory ErrorResponse({
    required String errorCode,
    required String message,
    String? jobId,
    required String requestId,
    required DateTime timestamp,
    @Default({}) Map<String, dynamic> detail,
  }) = _ErrorResponse;

  factory ErrorResponse.fromJson(Map<String, dynamic> json) =>
      _$ErrorResponseFromJson(json);
}

enum JobStatus { queued, processing, completed, failed }

@freezed
class JobStatusResponse with _$JobStatusResponse {
  const factory JobStatusResponse({
    required String jobId,
    required JobStatus status,
    required int progress,
    required DateTime createdAt,
    required DateTime updatedAt,
    String? resultUrl,
    String? errorMessage,
  }) = _JobStatusResponse;

  factory JobStatusResponse.fromJson(Map<String, dynamic> json) =>
      _$JobStatusResponseFromJson(json);
}
```

---

## Flutter ApiService 헤더 표준

```dart
// mobile/lib/services/api_service.dart
import 'package:dio/dio.dart';
import 'package:uuid/uuid.dart';

class ApiService {
  late final Dio _dio;
  final _uuid = const Uuid();

  ApiService() {
    _dio = Dio(BaseOptions(
      baseUrl: AppConfig.apiBaseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
    ));

    // 공통 요청 헤더 인터셉터
    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        // 모든 요청에 자동 추가
        options.headers['X-Request-ID'] = _uuid.v4();
        options.headers['X-App-Version'] = AppConfig.appVersion;
        options.headers['Accept'] = 'application/json';

        // job_id가 있으면 추가
        if (options.extra['jobId'] != null) {
          options.headers['X-Job-ID'] = options.extra['jobId'];
        }

        handler.next(options);
      },
      onError: (error, handler) {
        // 에러 응답을 ErrorResponse로 파싱
        if (error.response?.data != null) {
          try {
            final errorResponse = ErrorResponse.fromJson(
              error.response!.data as Map<String, dynamic>
            );
            // 에러 코드별 분기 처리
            _handleErrorCode(errorResponse.errorCode);
          } catch (_) {}
        }
        handler.next(error);
      },
    ));
  }

  void _handleErrorCode(String errorCode) {
    switch (errorCode) {
      case 'TOKEN_EXPIRED':
        // 토큰 갱신 로직
        break;
      case 'UNAUTHORIZED':
        // 로그인 화면으로 이동
        break;
    }
  }
}
```

---

## 금지 패턴

```python
# ❌ 에러 응답 구조 임의 생성
return {"error": "something went wrong"}   # 구조 없음
return {"message": "not found"}           # error_code 없음

# ❌ HTTP 상태코드만으로 에러 구분
raise HTTPException(status_code=400)      # body 없음

# ❌ 에러 코드 즉흥 생성
error_code="fileTooBig"                   # 목록에 없는 코드, camelCase
error_code="ERR_001"                      # 의미 없는 코드

# ✅ 올바른 패턴
raise VibeException(
    error_code="VIDEO_TOO_LARGE",
    message="파일 크기가 제한을 초과했습니다",
    status_code=400,
)
```

```dart
// ❌ 에러 응답 임의 파싱
final message = response.data['message'];  // 구조 가정

// ❌ 에러 코드 없이 상태코드만 확인
if (response.statusCode == 400) { ... }

// ✅ 올바른 패턴
final error = ErrorResponse.fromJson(response.data);
switch (error.errorCode) {
  case 'VIDEO_TOO_LARGE': ...
  case 'JOB_NOT_FOUND': ...
}
```
