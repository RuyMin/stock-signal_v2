---
name: vibe-framework-flutter
description: >
  Vibe Framework의 Flutter 모바일 앱 구조 및 코딩 규칙.
  Flutter 코드 작성, 새 기능 추가, 상태관리 구현 시 반드시 참조.
  이 문서의 구조를 벗어나는 Flutter 코드는 생성하지 말 것.
---

# Flutter Skill

## 상태관리: Riverpod 표준

이 프레임워크는 **Riverpod**을 표준 상태관리로 사용한다.
BLoC, Provider, GetX는 사용하지 않는다.

---

## Feature-First 디렉토리 구조

```
mobile/lib/
├── main.dart
├── core/
│   ├── config/
│   │   └── app_config.dart        # 환경변수 (API URL, Supabase 등)
│   ├── router/
│   │   └── app_router.dart        # GoRouter 기반 라우팅
│   ├── theme/
│   │   └── app_theme.dart         # 전체 테마 정의
│   └── di/
│       └── providers.dart         # 전역 Provider 등록
│
├── shared/
│   ├── widgets/                   # 재사용 위젯
│   └── utils/                     # 유틸 함수
│
├── models/                        # shared/models/ 에서 동기화
│   ├── job_model.dart
│   └── {feature}_model.dart
│
├── services/
│   ├── auth_service.dart          # Supabase Auth (로그인/회원가입/세션 관리)
│   ├── api_service.dart           # FastAPI HTTP 클라이언트 (Dio + 인증 인터셉터)
│   └── realtime_service.dart      # Supabase Realtime 구독
│
└── features/
    └── {feature_name}/
        ├── data/
        │   ├── repository.dart     # 데이터 접근 추상화
        │   └── datasource.dart     # 실제 API/DB 호출
        ├── domain/
        │   ├── models/             # 이 기능 전용 모델
        │   └── providers/          # Riverpod Provider 정의
        └── presentation/
            ├── screens/
            └── widgets/
```

---

## 환경 설정 (AppConfig)

```dart
// core/app_config.dart
enum AppEnvironment { dev, staging, prod }

class AppConfig {
  static late AppEnvironment environment;

  static void init(AppEnvironment env) {
    environment = env;
  }

  static String get apiBaseUrl => switch (environment) {
    AppEnvironment.dev     => 'http://localhost:8000',
    AppEnvironment.staging => 'https://staging.{domain}',
    AppEnvironment.prod    => 'https://{domain}',
  };

  static String get supabaseUrl => switch (environment) {
    AppEnvironment.dev     => 'http://localhost:8001',
    AppEnvironment.staging => 'https://staging.{domain}/supabase',
    AppEnvironment.prod    => 'https://{domain}/supabase',
  };

  static String get supabaseAnonKey => const String.fromEnvironment(
    'SUPABASE_ANON_KEY',
    defaultValue: 'dev-anon-key',
  );
}
```

```dart
// main.dart — 환경별 진입점
void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // 빌드 시 --dart-define=ENV=dev|staging|prod 로 주입
  const envStr = String.fromEnvironment('ENV', defaultValue: 'dev');
  final env = AppEnvironment.values.firstWhere(
    (e) => e.name == envStr,
    orElse: () => AppEnvironment.dev,
  );
  AppConfig.init(env);

  await Supabase.initialize(
    url: AppConfig.supabaseUrl,
    anonKey: AppConfig.supabaseAnonKey,
  );

  runApp(const ProviderScope(child: MyApp()));
}
```

```bash
# 빌드 명령어
flutter run --dart-define=ENV=dev                           # 개발
flutter run --dart-define=ENV=staging                       # 스테이징
flutter build apk --dart-define=ENV=prod                    # 운영 빌드
flutter build apk --dart-define=ENV=prod --dart-define=SUPABASE_ANON_KEY=eyJ...
```

---

## API 호출 표준 패턴

```dart
// services/api_service.dart
import 'package:dio/dio.dart';
import 'package:riverpod_annotation/riverpod_annotation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

@riverpod
ApiService apiService(ApiServiceRef ref) {
  return ApiService();
}

class ApiService {
  late final Dio _dio;

  ApiService() {
    _dio = Dio(BaseOptions(
      baseUrl: AppConfig.apiBaseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
    ));

    // 인증 인터셉터 — Supabase Auth 토큰 자동 추가
    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        final session = Supabase.instance.client.auth.currentSession;
        if (session != null) {
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
          Supabase.instance.client.auth.signOut();
          // TODO: GoRouter로 로그인 화면 이동
        }
        handler.next(error);
      },
    ));
    _dio.interceptors.add(LogInterceptor());
  }

  // Job 제출 — 즉시 job_id 반환
  Future<String> submitJob(String endpoint, Map<String, dynamic> payload) async {
    final response = await _dio.post(endpoint, data: payload);
    return response.data['job_id'] as String;
  }

  // Job 상태 조회
  Future<JobStatus> getJobStatus(String jobId) async {
    final response = await _dio.get('/jobs/$jobId');
    return JobStatus.fromJson(response.data);
  }
}
```

---

## Realtime 구독 표준 패턴

```dart
// services/realtime_service.dart
// Supabase Realtime으로 job 완료 이벤트 수신
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:riverpod_annotation/riverpod_annotation.dart';

@riverpod
Stream<JobStatus> jobStatusStream(JobStatusStreamRef ref, String jobId) {
  final supabase = Supabase.instance.client;
  return supabase
      .from('jobs')
      .stream(primaryKey: ['id'])
      .eq('id', jobId)
      .map((data) => JobStatus.fromJson(data.first));
}
```

---

## Feature Provider 표준 패턴

```dart
// features/{feature}/domain/providers/job_provider.dart
import 'package:riverpod_annotation/riverpod_annotation.dart';

part 'job_provider.g.dart';

@riverpod
class JobNotifier extends _$JobNotifier {
  @override
  AsyncValue<JobStatus?> build() => const AsyncValue.data(null);

  Future<void> submitJob(Map<String, dynamic> payload) async {
    state = const AsyncValue.loading();
    try {
      final apiService = ref.read(apiServiceProvider);
      final jobId = await apiService.submitJob('/reels/generate', payload);
      // Realtime 구독 시작
      ref.listen(
        jobStatusStreamProvider(jobId),
        (_, next) => state = next.whenData((s) => s),
      );
    } catch (e, st) {
      state = AsyncValue.error(e, st);
    }
  }
}
```

---

## UI 패턴: 비동기 작업 진행 표시

```dart
// 긴 AI 작업 (수 분)에 대한 표준 UI 패턴
class JobProgressWidget extends ConsumerWidget {
  final String jobId;
  const JobProgressWidget({required this.jobId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobStatus = ref.watch(jobStatusStreamProvider(jobId));
    
    return jobStatus.when(
      data: (status) => switch (status?.status) {
        'queued'     => const QueuedIndicator(),
        'processing' => ProgressIndicator(progress: status!.progress),
        'completed'  => ResultView(resultUrl: status!.resultUrl!),
        'failed'     => ErrorView(message: status!.errorMessage!),
        _            => const SizedBox.shrink(),
      },
      loading: () => const CircularProgressIndicator(),
      error: (e, _) => ErrorView(message: e.toString()),
    );
  }
}
```

---

## 파일 업로드 표준 패턴

```dart
// 영상 업로드 → job 제출 → Realtime으로 결과 수신
Future<void> uploadAndProcess(File videoFile) async {
  // 1. Multipart 업로드
  final formData = FormData.fromMap({
    'video': await MultipartFile.fromFile(videoFile.path),
    'guideline_id': guidelineId,
  });
  
  // 2. FastAPI에 제출 → job_id 즉시 반환
  final jobId = await apiService.submitJob('/reels/generate', formData);
  
  // 3. Realtime 구독으로 진행상황 추적
  // (JobNotifier가 자동으로 처리)
}
```

---

## 금지 패턴

```dart
// ❌ UI 레이어에서 직접 HTTP 호출
class MyScreen extends StatelessWidget {
  void _submit() async {
    final dio = Dio();
    await dio.post('/api/...');  // 금지 — service 레이어 사용
  }
}

// ❌ StatefulWidget으로 상태관리
class MyScreen extends StatefulWidget {
  String? _jobId;  // 금지 — Riverpod Provider 사용
}

// ❌ 긴 작업을 동기로 기다림
final result = await crew.process();  // 금지 — job_id + Realtime 패턴 사용

// ✅ 올바른 패턴
ref.read(jobNotifierProvider.notifier).submitJob(payload);
// UI는 jobStatusStreamProvider를 watch하여 자동 업데이트
```

---

## pubspec.yaml 표준 의존성

```yaml
dependencies:
  flutter_riverpod: ^2.5.0
  riverpod_annotation: ^2.3.0
  dio: ^5.4.0
  supabase_flutter: ^2.3.0
  go_router: ^13.2.0
  freezed_annotation: ^2.4.0
  json_annotation: ^4.8.0

dev_dependencies:
  riverpod_generator: ^2.4.0
  build_runner: ^2.4.0
  freezed: ^2.4.0
  json_serializable: ^6.7.0
```
