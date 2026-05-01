# Vibe Framework — 시작 가이드

> 새 프로젝트를 시작하거나 기존 프로젝트를 이어받을 때 반드시 이 문서를 먼저 읽는다.

## 사전 준비

이 프레임워크는 **Claude Code CLI** 환경에서 동작한다.

```
필수 환경:
- Claude Code CLI 설치 (claude 명령어 사용 가능)
- WSL2 또는 macOS/Linux 터미널
- Docker + Docker Compose 설치
- Git 설치
- Obsidian (Vault 문서 관리용)

시작 방법:
1. 프로젝트 루트에 이 프레임워크 파일들을 배치
2. 터미널에서 프로젝트 루트로 이동
3. claude 명령어로 Claude Code 실행
4. CLAUDE.md가 자동으로 로드됨
5. 슬래시 커맨드(/architect 등) 또는 직접 프롬프트로 페르소나 호출
```

---

## Step 1. .vibe/config.yml 설정

프로젝트 루트에 `.vibe/config.yml` 을 생성하고 아래 항목을 채운다.
아래는 **예시**이므로 본인 프로젝트에 맞게 수정한다.

**OS별 경로 작성법:**
```
Windows (WSL2):
  project_root: /mnt/f/projects/my-project     ← WSL2 경로 사용
  vault.path:   /mnt/d/Obsidian/MyVault
  ※ Windows 네이티브 경로(F:\projects\...)는 주석으로 병기 권장

macOS:
  project_root: /Users/username/projects/my-project
  vault.path:   /Users/username/Obsidian/MyVault

Linux:
  project_root: /home/username/projects/my-project
  vault.path:   /home/username/Obsidian/MyVault
```

```yaml
# .vibe/config.yml — 본인 프로젝트에 맞게 수정할 것

project:
  name: reels-generator          # 프로젝트 식별자 (영문, kebab-case)
  description: "협찬 릴스 자동 생성 서비스"

vault:
  path: /Users/username/Obsidian/MyVault   # Obsidian Vault 절대 경로
  project_folder: 프로젝트/릴스자동화        # Vault 내 이 프로젝트 폴더
  shared_folders:                           # 공통 참고 폴더 (선택)
    - 공통/기술스펙
    - 공통/팀컨벤션

stack:
  confirmed:
    mobile: flutter
    backend: fastapi
    ai: crewai
    db: postgresql
    cache: redis
    queue: kafka
    storage: minio
    realtime: supabase
  pending:                                  # 미확정 스택은 여기에
    - web_framework                         # 추후 결정
```

---

## Step 2. Vault 파일 준비 확인

config.yml에 지정한 `project_folder` 안에 아래 파일들이 있는지 확인한다.

```
필수 (없으면 Architect 시작 전 작성):
  ✅ PRD.{설명}.md       최소 1개 이상
  ✅ REVIEW.md           미결 검토사항 (없으면 빈 파일로 생성)

있으면 좋음:
  ⬜ SPEC.{설명}.md      기술 스펙
  ⬜ ARCH.{설명}.md      아키텍처 결정사항

스킵됨 (없어도 됨):
  ⬜ REF.{설명}.md       참고자료
  ⬜ DONE.{설명}.md      아카이브
```

**파일명 규칙 확인**
```
✅ PRD.기능정의.md
✅ SPEC.영상처리플로우.md
❌ 기획서.md              → PRD.기획서.md 로 변경
❌ spec_api.md            → SPEC.API설계.md 로 변경
```

---

## Step 3. Claude에게 전달할 시작 프롬프트

아래 템플릿을 복사해서 Claude에게 전달한다.

```
Vibe Framework로 새 프로젝트를 시작합니다.

## 설정 파일
[.vibe/config.yml 내용 붙여넣기]

## Vault 문서
[아래 파일들의 내용을 순서대로 붙여넣기]

### ARCH.{파일명}.md
{내용}

### SPEC.{파일명}.md
{내용}

### PRD.{파일명}.md
{내용}

### REVIEW.md (미결 섹션만)
{🔴 미결 섹션 내용}

---
Architect 페르소나로 위 문서를 분석하고 CONTEXT.md를 작성해주세요.
```

**파일 첨부 순서 (반드시 준수)**
```
1. ARCH.*    아키텍처 결정 먼저
2. SPEC.*    기술 스펙
3. PRD.*     기획 요구사항
4. REVIEW.md 미결 사항 (마지막)
```

---

## Step 4. 이어받기 (기존 프로젝트)

이미 진행 중인 프로젝트를 새 세션에서 이어받을 때.

```
Vibe Framework 프로젝트를 이어받습니다.

## 현재 상태
[CONTEXT.md 전체 내용 붙여넣기]

## 에러 이력 (있는 경우)
[ERROR_LOG.md 내용 붙여넣기]

## Vault 변경사항 (마지막 세션 이후 수정된 파일만)
[변경된 파일 내용 붙여넣기]

---
{원하는 페르소나}로 {작업 내용}을 진행해주세요.
```

**이어받기 시 Vault 파일은 변경된 것만 전달한다.**
전체를 다시 읽히지 않는다 → 토큰 절약.

---

## Step 5. 페르소나 호출 방법

### 슬래시 커맨드 (권장)

Claude Code에서 아래 커맨드로 즉시 페르소나를 전환할 수 있다.

```
/architect           Architect — 설계 + CONTEXT.md + TEST_SPEC.md
/backend             Backend Engineer — FastAPI + DB + Auth
/ai-engineer         AI Engineer — CrewAI + Workers
/mobile              Mobile Engineer — Flutter 앱
/devops              DevOps Engineer — Docker 통합 + CI/CD
/qa-test             QA Engineer 모드 B — 테스트 작성/실행
/qa-error            QA Engineer 모드 A — 에러 분석/수정
/framework-update    Framework Updater — SKILL 업데이트
```

### 직접 프롬프트

슬래시 커맨드 대신 직접 입력해도 동작한다.

```
"Architect 페르소나로 위 문서를 분석하고 CONTEXT.md를 작성해주세요"

"Backend Engineer 페르소나로 CONTEXT.md 기반으로 백엔드를 구현해주세요"

"AI Engineer 페르소나로 CrewAI Crew를 구현해주세요"

"Mobile Engineer 페르소나로 Flutter 앱을 구현해주세요"

"DevOps Engineer 페르소나로 Docker Compose를 통합해주세요"

"QA Engineer 페르소나로 TEST_SPEC.md 기반으로 테스트 코드 작성해주세요"

"QA Engineer 페르소나로 이 에러를 분석해주세요: [에러 메시지]"
```

---

## Step 5-1. 개발 환경 기동 시점

페르소나별 작업과 Docker 기동의 관계:

```
Architect        → Docker 불필요 (설계만)
Backend Engineer → Docker 불필요 (코드 작성만)
AI Engineer      → Docker 불필요 (코드 작성만)
Mobile Engineer  → Docker 불필요 (코드 작성만)
DevOps Engineer  → Docker 필요 (전체 기동 검증)
QA (모드 B)      → Docker 필요 (통합/E2E 테스트)
```

DevOps Engineer가 완료되면 아래 명령으로 기동한다:

```bash
# 개발 환경 (로컬)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# 스테이징 (OCI VM)
docker compose -f docker-compose.yml -f docker-compose.staging.yml --env-file .env.staging up -d

# 운영 (OCI VM)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d
```

환경 분리 상세 → `skills/structure/DOCKER_SKILL.md` "환경 분리" 섹션 참조.

---

## 전체 플로우 요약

```
[Obsidian Vault]
  PRD.* / SPEC.* / ARCH.* / REVIEW.md
        ↓ 접두사 규칙으로 선택적 읽기
[Claude 시작 프롬프트]
  .vibe/config.yml + Vault 문서
        ↓
[Architect]
  → CONTEXT.md + TEST_SPEC.md 생성
  → shared/schemas/ 정의
  → docker-compose.yml 뼈대
        ↓
[Backend Engineer]
  → FastAPI + DB + Kafka + Auth 구현
        ↓
[AI Engineer] ──── 병렬 ──── [Mobile Engineer]
  → CrewAI + Workers              → Flutter 앱
        ↓
[DevOps Engineer]
  → Docker 통합 + CI/CD + 전체 기동 검증
        ↓
[QA Engineer — 모드 B]
  → TEST_SPEC.md 기반 테스트 코드 작성 + 실행
        ↓
[배포]
  → staging 검증 → prod 배포
        ↓
     🚀 서비스 완성

에러 발생 시:
[QA Engineer — 모드 A]
  → Grafana 로그 분석
  → 최소 수정 + ERROR_LOG.md + TIL 작성
```

---

## Step 6. 프로젝트 완료 — 학습 루프

프로젝트가 완료되면 아래 순서로 프레임워크를 업그레이드한다.

### 6-1. TIL 작성 (개발 중 수시로)

에러를 해결할 때마다 즉시 작성한다. 나중에 몰아서 쓰지 않는다.

```
TIL_TEMPLATE.md 복사
→ TIL.{주제}.md 로 저장 (프로젝트 폴더 안에)
→ "SKILL 반영 필요 여부" 체크 필수
```

### 6-2. RETRO 작성 (프로젝트 완료 직후)

```
RETRO_TEMPLATE.md 복사
→ RETRO.{프로젝트명}.md 로 저장
→ TIL 파일 목록 정리
→ Keep / Problem / Try 작성
→ SKILL 업데이트 우선순위 결정
```

### 6-3. Framework Updater 실행

```
Framework Updater 페르소나로 프레임워크를 업데이트해주세요.

## 입력 파일
### RETRO.{프로젝트명}.md
{내용}

### TIL.{주제1}.md
{내용}

### TIL.{주제2}.md
{내용}

## 업데이트 대상 SKILL 파일들
### CREWAI_SKILL.md (현재)
{현재 내용}

### MINIO_SKILL.md (현재)
{현재 내용}
```

### 6-4. 다음 프로젝트 시작

Framework Updater 완료 후 업데이트된 SKILL 파일로 다음 프로젝트를 시작한다.
RETRO의 "다음 프로젝트 시작 전 주의사항"을 Architect에게 반드시 전달한다.

---

## 전체 플로우 (학습 루프 포함)

```
[Obsidian Vault]
  PRD.* / SPEC.* / ARCH.* / REVIEW.md
  + TIL.* (이전 프로젝트 지식)
  + RETRO.* 주의사항 (이전 프로젝트 회고)
        ↓
[Architect] → CONTEXT.md + TEST_SPEC.md
        ↓
[Backend] → [AI Engineer] / [Mobile] → [DevOps] → [QA 테스트] → [배포]
        ↓
     🚀 서비스 완성
        ↓
  개발 중 에러 해결 → TIL.*.md 즉시 작성
        ↓
  프로젝트 완료 → RETRO.*.md 작성
        ↓
[Framework Updater]
  TIL + RETRO → SKILL 업데이트
        ↓
  다음 프로젝트는 더 똑똑한 프레임워크로 시작 🔄
```

```
회의/검토 중 결정된 사항
  → 즉시 해당 PRD/SPEC/ARCH 파일에 반영
  → REVIEW.md 완료 섹션으로 이동

결정 보류된 사항
  → REVIEW.md 미결 섹션에 추가
  → 날짜와 영향 범위 반드시 기록

Claude 세션 시작 시
  → REVIEW.md 미결 섹션만 전달
  → 완료 섹션은 전달하지 않음
```

---

## 자주 있는 상황별 가이드

### 개발 중 스펙이 바뀌었을 때

```
1. 해당 PRD/SPEC 파일 즉시 업데이트
2. CONTEXT.md의 "다음 작업 시 주의사항" 업데이트
3. 영향받는 페르소나에게 변경된 파일만 전달
4. "SPEC.영상처리플로우.md가 변경됐습니다. 
   AI Engineer 페르소나로 Worker를 수정해주세요"
```

### 미결 사항이 결정됐을 때

```
1. PRD/SPEC/ARCH 파일 업데이트
2. REVIEW.md 미결 → 완료 섹션으로 이동
3. 다음 Claude 세션에 변경된 파일만 전달
```

### 새 기능을 추가할 때

```
1. PRD.{기능명}.md 또는 기존 PRD에 추가
2. 필요시 SPEC.{기술스펙}.md 추가
3. REVIEW.md에 미결 사항 있으면 추가
4. Architect 페르소나로 기능 설계 시작
```

---

## .gitignore 권장 설정

```gitignore
# Vibe Framework
.vibe/.env
.vibe/secrets/

# 실제 .env 파일
.env
*.env
!.env.example
```

`.vibe/config.yml` 은 커밋한다 (vault path는 팀원마다 다를 수 있으므로 주석 처리 권장).
