---
name: vibe-framework-supabase
description: >
  Vibe Framework의 Supabase 사용 규칙.
  1) Auth: 회원가입/로그인/JWT 발급을 Supabase Auth에 위임.
  2) Realtime: Flutter Realtime 구독, Python에서 완료 이벤트 발행, self-hosted 설정.
  인증 관련 코드 생성 시, Realtime 구독/푸시 코드 생성 시 참조.
  폴링 방식으로 작업 완료를 확인하는 코드는 절대 생성 금지.
---

# Supabase Skill

## 핵심 원칙

1. **인증은 Supabase Auth에 위임한다** — 자체 JWT 발급/비밀번호 해싱 금지
2. **Backend는 JWT 서명 검증만 한다** — 토큰 발급/갱신은 Flutter ↔ Supabase 직접 통신
3. **Flutter는 절대 폴링하지 않는다** — 완료 이벤트는 Realtime으로만 수신
4. **Realtime의 소스는 PostgreSQL** — jobs 테이블 변경을 구독하는 방식
5. **이벤트는 Worker가 DB 업데이트로 발행** — 별도 pub/sub 아님, DB 변경이 곧 이벤트
6. **구독은 job_id 단위로 필터링** — 전체 테이블 구독 금지
7. **연결 끊김 시 자동 재연결 구현** — 필수

---

## Part 1. 인증 (Supabase Auth)

### 인증 흐름

```
Flutter                    Supabase Auth              Backend (FastAPI)
  │                            │                          │
  ├── 회원가입/로그인 요청 ──→  │                          │
  │                            ├── JWT 발급               │
  │  ←── access_token ─────────┤   (access + refresh)     │
  │      + refresh_token       │                          │
  │                            │                          │
  ├── API 요청 ────────────────┼── Authorization: Bearer ─→│
  │                            │                          ├── JWT 서명 검증
  │                            │                          │   (Supabase JWT_SECRET 사용)
  │  ←── 응답 ─────────────────┼──────────────────────────┤
  │                            │                          │
  ├── 토큰 만료 시             │                          │
  ├── 토큰 갱신 요청 ────────→ │                          │
  │  ←── 새 access_token ──────┤                          │
  │                            │                          │
```

**핵심: Backend는 토큰을 발급하지 않는다. 검증만 한다.**

### Flutter 인증 구현

```dart
// mobile/lib/services/auth_service.dart
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:riverpod_annotation/riverpod_annotation.dart';

part 'auth_service.g.dart';

@riverpod
class AuthService extends _$AuthService {
  SupabaseClient get _supabase => Supabase.instance.client;

  @override
  Stream<AuthState> build() {
    return _supabase.auth.onAuthStateChange;
  }

  // 이메일 회원가입
  Future<AuthResponse> signUp(String email, String password) async {
    return await _supabase.auth.signUp(
      email: email,
      password: password,
    );
  }

  // 이메일 로그인
  Future<AuthResponse> signIn(String email, String password) async {
    return await _supabase.auth.signInWithPassword(
      email: email,
      password: password,
    );
  }

  // 로그아웃
  Future<void> signOut() async {
    await _supabase.auth.signOut();
  }

  // 현재 access_token 가져오기 (API 요청용)
  String? get accessToken => _supabase.auth.currentSession?.accessToken;

  // 현재 사용자 ID
  String? get userId => _supabase.auth.currentUser?.id;
}
```

### Flutter API 서비스에 인증 헤더 자동 추가

```dart
// mobile/lib/services/api_service.dart
import 'package:dio/dio.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

class ApiService {
  late final Dio _dio;

  ApiService() {
    _dio = Dio(BaseOptions(
      baseUrl: AppConfig.apiBaseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
    ));

    // 인증 인터셉터 — 모든 요청에 Bearer 토큰 자동 추가
    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        final session = Supabase.instance.client.auth.currentSession;
        if (session != null) {
          // 토큰 만료 확인 및 자동 갱신
          if (session.isExpired) {
            await Supabase.instance.client.auth.refreshSession();
            final newSession = Supabase.instance.client.auth.currentSession;
            options.headers['Authorization'] = 'Bearer ${newSession?.accessToken}';
          } else {
            options.headers['Authorization'] = 'Bearer ${session.accessToken}';
          }
        }
        handler.next(options);
      },
      onError: (error, handler) {
        final errorCode = error.response?.data?['error_code'];
        if (errorCode == 'TOKEN_EXPIRED' || errorCode == 'UNAUTHORIZED') {
          // 세션 무효화 → 로그인 화면 이동
          Supabase.instance.client.auth.signOut();
        }
        handler.next(error);
      },
    ));
  }
}
```

### Backend JWT 검증 미들웨어

```python
# backend/core/auth.py
import os
import jwt
import structlog
from fastapi import Request, HTTPException

logger = structlog.get_logger()

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

async def verify_jwt(request: Request) -> dict:
    """
    Supabase Auth가 발급한 JWT를 검증한다.
    토큰 발급/갱신은 하지 않는다 — 검증만.
    
    Returns:
        dict: JWT payload (sub=user_id, email, role 등)
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={
            "error_code": "UNAUTHORIZED",
            "message": "인증 토큰이 필요합니다",
        })
    
    token = auth_header.split(" ")[1]
    
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail={
            "error_code": "TOKEN_EXPIRED",
            "message": "토큰이 만료되었습니다. 갱신해주세요.",
        })
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail={
            "error_code": "UNAUTHORIZED",
            "message": "유효하지 않은 토큰입니다",
        })
```

### FastAPI Depends로 인증 적용

```python
# backend/core/dependencies.py
from fastapi import Depends
from core.auth import verify_jwt

async def get_current_user(payload: dict = Depends(verify_jwt)) -> dict:
    """인증된 사용자 정보를 반환한다."""
    return {
        "user_id": payload["sub"],
        "email": payload.get("email"),
        "role": payload.get("role", "authenticated"),
    }
```

```python
# backend/routers/{feature}.py
from core.dependencies import get_current_user

@router.post("/reels/generate", status_code=202)
async def generate_reels(
    user: dict = Depends(get_current_user),  # ← 인증 필수
    ...
):
    # user["user_id"]로 job 소유자 기록
    ...
```

### 인증 불필요 엔드포인트

```python
# 아래 엔드포인트는 get_current_user를 사용하지 않는다
GET  /health          # 헬스체크
# 그 외 모든 엔드포인트는 인증 필수
```

### jobs 테이블에 user_id 추가

```sql
-- infra/postgres/init.sql 에 user_id 컬럼 추가
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,      -- Supabase Auth user ID
    job_type        VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'queued',
    ...
);

-- user_id 인덱스 (내 작업 목록 조회용)
CREATE INDEX idx_jobs_user_id ON jobs(user_id);
```

### 인증 관련 환경변수

```env
# .env.example에 추가
SUPABASE_JWT_SECRET=your-super-secret-jwt-token   # Supabase와 동일한 값
# Backend는 이것만 있으면 JWT 검증 가능
# 토큰 발급은 Supabase가 하므로 다른 키 불필요
```

### requirements.txt 추가

```
PyJWT==2.8.0           # JWT 디코딩/검증용
```

---

## Part 2. Realtime (Supabase Realtime)

## 동작 방식

```
Worker 처리 완료
  ↓
PostgreSQL UPDATE jobs SET status='completed', result_key='...'
  ↓
Supabase Realtime (PostgreSQL WAL 감지)
  ↓
Flutter Supabase 클라이언트 (구독 중)
  ↓
UI 자동 업데이트
```

별도의 pub/sub 서버가 필요 없다.
**PostgreSQL의 jobs 테이블이 이벤트 버스 역할을 한다.**

---

## PostgreSQL jobs 테이블 (Realtime 활성화 필수)

```sql
-- infra/postgres/init.sql
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type        VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'queued',
    progress        INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    result_key      TEXT,           -- MinIO object key
    error_msg       TEXT,
    metadata        JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Realtime 활성화 (self-hosted Supabase 필수 설정)
ALTER TABLE jobs REPLICA IDENTITY FULL;

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 인덱스
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);
```

---

## Python에서 job 상태 업데이트 (Worker)

```python
# core/db.py — Worker/CrewAI에서 상태 업데이트
import asyncpg
import os
import structlog

logger = structlog.get_logger()

async def update_job_status(
    job_id: str,
    status: str,
    progress: int = None,
    result_key: str = None,
    error_msg: str = None,
):
    """
    job 상태 업데이트 — Supabase Realtime이 자동으로 Flutter에 푸시.
    Worker의 모든 단계 완료/실패 시 반드시 호출.
    """
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    try:
        updates = {"status": status}
        if progress is not None:
            updates["progress"] = progress
        if result_key is not None:
            updates["result_key"] = result_key
        if error_msg is not None:
            updates["error_msg"] = error_msg

        set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
        values = [job_id] + list(updates.values())

        await conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = $1",
            *values,
        )
        logger.info("job_status_updated",
            job_id=job_id,
            status=status,
            progress=progress,
        )
    finally:
        await conn.close()
```

### Worker에서 단계별 진행률 업데이트

```python
# workers/{name}/processor.py
async def process(event: dict) -> dict:
    job_id = event["job_id"]

    # 시작
    await update_job_status(job_id, "processing", progress=0)

    # 단계 1: 다운로드 (0~20%)
    await download_file(...)
    await update_job_status(job_id, "processing", progress=20)

    # 단계 2: 분석 (20~50%)
    analysis = await analyze_video(...)
    await update_job_status(job_id, "processing", progress=50)

    # 단계 3: 편집 (50~90%)
    output = await edit_video(...)
    await update_job_status(job_id, "processing", progress=90)

    # 완료
    result_key = await upload_result(output)
    await update_job_status(
        job_id,
        status="completed",
        progress=100,
        result_key=result_key,
    )

    return {"result_key": result_key}
```

---

## Flutter Realtime 구독 표준

### RealtimeService 구현

```dart
// mobile/lib/services/realtime_service.dart
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:riverpod_annotation/riverpod_annotation.dart';
import '../models/job_model.dart';

part 'realtime_service.g.dart';

@riverpod
Stream<JobStatus> jobStatusStream(
  JobStatusStreamRef ref,
  String jobId,
) {
  final supabase = Supabase.instance.client;

  return supabase
      .from('jobs')
      .stream(primaryKey: ['id'])
      .eq('id', jobId)
      .map((rows) {
        if (rows.isEmpty) throw Exception('Job not found: $jobId');
        return JobStatus.fromJson(rows.first);
      });
}
```

### UI에서 Realtime 구독 사용

```dart
// features/{name}/presentation/screens/job_progress_screen.dart
class JobProgressScreen extends ConsumerWidget {
  final String jobId;
  const JobProgressScreen({required this.jobId, super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobStatus = ref.watch(jobStatusStreamProvider(jobId));

    return jobStatus.when(
      data: (status) {
        return switch (status.status) {
          'queued'     => const _QueuedView(),
          'processing' => _ProcessingView(progress: status.progress),
          'completed'  => _CompletedView(jobId: jobId, resultKey: status.resultKey!),
          'failed'     => _FailedView(error: status.errorMsg ?? '알 수 없는 오류'),
          _            => const SizedBox.shrink(),
        };
      },
      loading: () => const CircularProgressIndicator(),
      error: (e, _) => _ErrorView(message: e.toString()),
    );
  }
}

// 진행률 표시
class _ProcessingView extends StatelessWidget {
  final int progress;
  const _ProcessingView({required this.progress});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        LinearProgressIndicator(value: progress / 100),
        Text('처리 중... $progress%'),
      ],
    );
  }
}
```

### Supabase 초기화 (main.dart)

```dart
// mobile/lib/main.dart
import 'package:supabase_flutter/supabase_flutter.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await Supabase.initialize(
    url: AppConfig.supabaseUrl,       // http://localhost:8001
    anonKey: AppConfig.supabaseAnonKey,
    realtimeClientOptions: const RealtimeClientOptions(
      eventsPerSecond: 10,
    ),
  );

  runApp(const ProviderScope(child: MyApp()));
}
```

---

## self-hosted Supabase Docker 구성

```yaml
# docker-compose.yml
  supabase:
    image: supabase/supabase-local:latest
    container_name: vibe-supabase
    networks: [vibe-net]
    depends_on:
      postgres: {condition: service_healthy}
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      JWT_SECRET: ${SUPABASE_JWT_SECRET}
      ANON_KEY: ${SUPABASE_ANON_KEY}
      SERVICE_KEY: ${SUPABASE_SERVICE_KEY}
    ports:
      - "8001:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5
```

> self-hosted Supabase 대신 **Supabase 클라우드 무료 플랜**을 개발 중에 사용하는 것도 가능.
> 단, 로컬 완전 컨테이너화 원칙에 따라 프로덕션은 self-hosted 권장.

---

## 환경변수

```env
# Supabase
SUPABASE_URL=http://supabase:8000
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
SUPABASE_JWT_SECRET=your-super-secret-jwt-token
```

---

## infra/supabase/config.toml

```toml
[api]
enabled = true
port = 8000

[db]
port = 5432

[realtime]
enabled = true
# jobs 테이블 Realtime 활성화
[realtime.tables]
jobs = { schema = "public", filter = "id=eq.*" }
```

---

## 금지 패턴

```dart
// ❌ 폴링으로 완료 확인
Timer.periodic(Duration(seconds: 3), (timer) async {
  final status = await apiService.getJobStatus(jobId);
  if (status == 'completed') timer.cancel();
});

// ❌ 전체 jobs 테이블 구독
supabase.from('jobs').stream(primaryKey: ['id'])  // job_id 필터 없음

// ❌ Flutter에서 직접 PostgreSQL 연결
// Supabase를 통하지 않고 DB 직접 접근

// ✅ 올바른 패턴
ref.watch(jobStatusStreamProvider(jobId))  // job_id 단위 구독
```

```python
# ❌ Worker 완료 후 상태 업데이트 생략
result = await process(event)
# update_job_status 호출 없이 종료 → Flutter가 완료를 모름

# ❌ 에러 발생 시 상태 업데이트 생략
except Exception as e:
    raise  # failed 상태 업데이트 없이 raise → Flutter가 실패를 모름

# ✅ 올바른 패턴
try:
    result = await process(event)
    await update_job_status(job_id, "completed", progress=100, result_key=result["key"])
except Exception as e:
    await update_job_status(job_id, "failed", error_msg=str(e))
    raise
```

---

## 인증 금지 패턴

```python
# ❌ Backend에서 JWT 발급
token = jwt.encode(payload, SECRET, algorithm="HS256")  # 절대 금지
# → Supabase Auth가 발급. Backend는 검증만.

# ❌ Backend에서 비밀번호 직접 처리
hashed = bcrypt.hashpw(password, salt)  # 절대 금지
# → Supabase Auth가 처리.

# ❌ Backend에서 회원가입/로그인 엔드포인트 생성
@router.post("/auth/login")  # 절대 금지
# → Flutter가 Supabase Auth SDK로 직접 처리.

# ❌ Backend에서 refresh token 처리
@router.post("/auth/refresh")  # 절대 금지
# → Flutter가 Supabase SDK의 refreshSession()으로 직접 처리.
```

```dart
// ❌ Flutter에서 자체 인증 로직 구현
final response = await dio.post('/auth/login', data: {...});  // 절대 금지
// → Supabase.instance.client.auth.signInWithPassword() 사용

// ❌ access_token을 SharedPreferences에 직접 저장
await prefs.setString('token', token);  // 절대 금지
// → Supabase SDK가 세션을 자동 관리
```
