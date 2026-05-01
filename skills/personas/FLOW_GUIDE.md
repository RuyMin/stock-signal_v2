---
name: persona-flow-guide
description: >
  Vibe Framework 개발 플로우 전체 가이드.
  어떤 페르소나를 어떤 순서로 호출해야 할지 모를 때 참조.
  새 프로젝트 시작 시 반드시 이 문서를 먼저 읽을 것.
---

# 개발 플로우 가이드

## 전체 플로우 한눈에

```
[Obsidian Vault]
  PRD.* / SPEC.* / ARCH.* / REVIEW.md
  + TIL.* + RETRO.* (이전 프로젝트 지식)
    │
    ▼
┌─────────────┐
│  ARCHITECT  │  ← 항상 첫 번째. 코드 없음. 설계만.
└──────┬──────┘
       │ CONTEXT.md + shared/schemas/ 출력
       ▼
┌─────────────────┐
│ BACKEND ENGINEER│  ← 단독 실행. API/DB/Kafka 구현.
└────────┬────────┘
         │ API 스펙 + Kafka 토픽 확정
         ▼
┌────────┴────────┐
│  병렬 진행 가능  │
├─────────────────┤
│  AI ENGINEER    │  ← CrewAI + Workers
│  MOBILE ENGINEER│  ← Flutter 앱
└────────┬────────┘
         │ 모든 모듈 완료
         ▼
┌─────────────────┐
│ DEVOPS ENGINEER │  ← Docker 통합 + 기동 검증.
└────────┬────────┘
         │ 전체 기동 확인
         ▼
┌─────────────────┐
│ QA ENGINEER (B) │  ← TEST_SPEC.md 기반 테스트 작성 + 실행
└────────┬────────┘
         │
         ▼
      🚀 완료
    ↙         ↘
에러 발생 시   개발 중 배움
    ↓              ↓
┌──────────────┐   TIL.*.md 즉시 작성
│QA ENGINEER(A)│
└──────────────┘
         │ 프로젝트 완료 후
         ▼
┌──────────────────┐
│ FRAMEWORK UPDATER│  ← TIL + RETRO → SKILL 업데이트
└──────────────────┘
         │
         ▼
   🔄 다음 프로젝트
   (더 똑똑한 프레임워크)
```

---

## 페르소나 호출 방법

각 페르소나는 이렇게 호출한다.

```
"Architect 페르소나로 이 기획서 분석해줘: [기획서 내용]"

"Backend Engineer 페르소나로 CONTEXT.md 기반으로 백엔드 구현해줘"

"AI Engineer 페르소나로 CrewAI Crew 구현해줘"

"Mobile Engineer 페르소나로 Flutter 앱 구현해줘"

"DevOps Engineer 페르소나로 Docker Compose 통합해줘"

"QA Engineer 페르소나로 TEST_SPEC.md 기반으로 테스트 코드 작성해줘"

"QA Engineer 페르소나로 이 에러 분석하고 수정해줘: [에러 메시지]"

"Framework Updater 페르소나로 프레임워크 업데이트해줘: [RETRO + TIL 파일들]"
```

---

## 단계별 입력/출력 요약

| 페르소나 | 필요한 입력 | 출력 |
|---------|-----------|------|
| Architect | config.yml + Vault 문서 (PRD/SPEC/ARCH 전체, REVIEW 미결만, TIL 중 SKILL 반영 필요 표시된 것만, RETRO 주의사항 섹션만) | CONTEXT.md, TEST_SPEC.md, shared/schemas/, topics.yml, docker-compose.yml(뼈대), 로그 인프라 설정, .env.example |
| Backend Engineer | CONTEXT.md, shared/schemas/ | backend/, infra/postgres/ |
| AI Engineer | CONTEXT.md, Kafka 토픽 목록 | crewai/, workers/ |
| Mobile Engineer | CONTEXT.md, API 스펙 | mobile/, shared/models/ |
| DevOps Engineer | CONTEXT.md, 모든 Dockerfile | docker-compose.yml, docker-compose.prod.yml, .env.example, .github/workflows/, infra/nginx/ |
| QA Engineer (모드 A) | CONTEXT.md, ERROR_LOG.md, TIL.* | 수정 코드, 업데이트된 로그, TIL.{에러주제}.md |
| QA Engineer (모드 B) | CONTEXT.md, TEST_SPEC.md, 구현된 코드 | tests/, mobile/test/, 테스트 실행 결과 |
| Framework Updater | RETRO.*.md, TIL.*.md, 현재 SKILL 파일들 | 업데이트된 SKILL, config.yml 버전업 |

---

## 자주 있는 상황별 가이드

### 새 기능 추가 시

```
1. CONTEXT.md 읽기
2. Architect 페르소나 → 기능 설계 (shared/schemas 추가)
3. Backend Engineer → 새 엔드포인트 추가
4. AI Engineer → 새 Crew/Worker 추가 (필요 시)
5. Mobile Engineer → 새 Feature 추가
6. DevOps Engineer → docker-compose.yml 업데이트 (필요 시)
```

### 에러 수정 후 재시작 위치

```
에러 발생 모듈        재시작 페르소나
backend/            → Backend Engineer
crewai/ / workers/  → AI Engineer
mobile/             → Mobile Engineer
docker-compose.yml  → DevOps Engineer
```

### 페르소나 중간에 맥락을 잃었을 때

```
1. CONTEXT.md 다시 읽기
2. ERROR_LOG.md 확인
3. 현재 페르소나의 SKILL.md 다시 읽기
4. 출력물 체크리스트에서 완료된 항목 확인
5. 미완료 항목부터 재개
```

---

## CONTEXT.md가 프로젝트의 중심이다

모든 페르소나는 CONTEXT.md를 읽고 시작하고, 업데이트하고 종료한다.
CONTEXT.md가 최신 상태를 반영하고 있으면 어떤 세션에서 시작하든 문제없다.

```
세션 시작 시 Claude에게 전달할 것:
1. CONTEXT.md (필수)
2. ERROR_LOG.md (에러 있을 경우)
3. 현재 작업할 페르소나 명시
```
