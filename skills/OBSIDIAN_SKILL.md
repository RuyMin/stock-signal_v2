---
name: vibe-framework-obsidian
description: >
  Vibe Framework의 Obsidian Vault 연동 규칙.
  프로젝트 문서 명명규칙, 파일별 읽기 전략, 토큰 절약 원칙 정의.
  모든 페르소나가 Vault 파일을 읽을 때 반드시 참조.
  이 규칙 없이 Vault 파일 전체를 무분별하게 읽는 것은 금지.
---

# Obsidian Skill

## 핵심 원칙

1. **파일명 접두사로만 읽기 여부 결정** — 내용을 열어보기 전에 접두사로 판단
2. **페르소나별 필요한 접두사만 읽음** — 모든 파일을 다 읽지 않는다
3. **DONE.* 은 항상 스킵** — 완료/아카이브 파일은 어떤 페르소나도 읽지 않음
4. **REVIEW.md 는 미결 섹션만** — 완료 섹션은 읽지 않음
5. **REF.* 는 명시적 요청 시에만** — 기본적으로 스킵

---

## Vault 폴더 구조

```
{Vault}/
└── {프로젝트 폴더}/          # .vibe/config.yml에 지정
    ├── PRD.기능정의.md
    ├── PRD.사용자시나리오.md
    ├── SPEC.영상처리플로우.md
    ├── SPEC.API설계.md
    ├── ARCH.모듈구조.md
    ├── REVIEW.md              # 단 하나, 미결 검토사항
    ├── TIL.CrewAI타임아웃.md  # 에러 해결 즉시 작성
    ├── TIL.MinIO멀티파트.md
    ├── RETRO.릴스자동화.md    # 프로젝트 완료 후 작성
    ├── REF.FFmpeg명령어.md
    └── DONE.초기기획_v1.md
```

---

## 파일 접두사 체계

| 접두사 | 역할 | 설명 |
|--------|------|------|
| `PRD` | 기획 / 요구사항 | 기능 정의, 사용자 시나리오, 비기능 요구사항 |
| `SPEC` | 기술 스펙 | API 설계, 데이터 구조, 처리 플로우 |
| `ARCH` | 아키텍처 결정 | 모듈 구조, 기술 선택 근거, 설계 원칙 |
| `REVIEW` | 미결 검토사항 | 결정 보류 중인 항목 (파일 하나만 유지) |
| `TIL` | 오늘 배운 것 | 에러 해결, 기술 발견, 패턴 (에러 해결 즉시 작성) |
| `RETRO` | 프로젝트 회고 | 완료 후 작성, SKILL 업데이트 트리거 |
| `REF` | 참고자료 | 외부 문서, 명령어 모음, 기술 노트 |
| `DONE` | 완료 / 아카이브 | 더 이상 유효하지 않은 문서 |

---

## 페르소나별 읽기 전략

```
Architect
  ✅ PRD.*    (전체)
  ✅ SPEC.*   (전체)
  ✅ ARCH.*   (전체)
  ✅ REVIEW.md (🔴 미결 섹션만)
  ✅ TIL.*    (SKILL 반영 필요 표시된 것만)
  ✅ RETRO.*  (다음 프로젝트 주의사항 섹션만)
  ❌ REF.*    (스킵 — 필요 시 명시적 요청)
  ❌ DONE.*   (항상 스킵)

Backend Engineer
  ✅ SPEC.*   (전체)
  ✅ ARCH.*   (전체)
  ✅ REVIEW.md (🔴 미결 중 backend 관련만)
  ✅ TIL.*    (카테고리: backend/infra 관련만)
  ❌ PRD.*    (스킵 — CONTEXT.md로 대체)
  ❌ RETRO.*  (스킵 — Architect가 주의사항 반영)
  ❌ REF.*    (스킵)
  ❌ DONE.*   (항상 스킵)

AI Engineer
  ✅ SPEC.*   (전체)
  ✅ ARCH.*   (전체)
  ✅ REVIEW.md (🔴 미결 중 ai/crewai 관련만)
  ✅ TIL.*    (카테고리: crewai/worker 관련만)
  ❌ PRD.*    (스킵)
  ❌ RETRO.*  (스킵)
  ❌ REF.*    (스킵)
  ❌ DONE.*   (항상 스킵)

Mobile Engineer
  ✅ PRD.*    (사용자 시나리오 관련만)
  ✅ REVIEW.md (🔴 미결 중 mobile 관련만)
  ✅ TIL.*    (카테고리: mobile 관련만)
  ❌ SPEC.*   (스킵 — API 스펙은 CONTEXT.md로)
  ❌ RETRO.*  (스킵)
  ❌ REF.*    (스킵)
  ❌ DONE.*   (항상 스킵)

DevOps Engineer
  ✅ ARCH.*   (전체)
  ✅ REVIEW.md (🔴 미결 중 infra 관련만)
  ✅ TIL.*    (카테고리: infra 관련만)
  ❌ PRD.*    (스킵)
  ❌ SPEC.*   (스킵)
  ❌ RETRO.*  (스킵)
  ❌ DONE.*   (항상 스킵)

QA Engineer
  ✅ REVIEW.md (🔴 미결 전체)
  ✅ TIL.*    (전체 — 유사 에러 패턴 참고용)
  ❌ 나머지    (CONTEXT.md + ERROR_LOG.md로 충분)

Framework Updater
  ✅ TIL.*    (전체 — 분석 대상)
  ✅ RETRO.*  (전체 — 분석 대상)
  ❌ 나머지    (SKILL 파일 직접 읽음)
```

---

## REVIEW.md 표준 구조

```markdown
# REVIEW.md

> 미결 검토사항 관리 파일.
> 결정된 사항은 즉시 해당 PRD/SPEC/ARCH 파일에 반영하고
> 이 파일의 ✅ 완료 섹션으로 이동한다.
> Claude는 🔴 미결 섹션만 읽는다.

---

## 🔴 미결 (검토 필요)

### [YYYY-MM-DD] {검토 항목 제목}
- **내용**: 무엇을 결정해야 하는가
- **배경**: 왜 보류됐는가
- **영향**: 어느 모듈 / 파일에 영향을 주는가
- **기한**: 언제까지 결정해야 하는가 (선택)

---

## ✅ 완료 (반영됨)

### [YYYY-MM-DD] {결정 항목 제목}
- **결정**: 무엇으로 결정됐는가
- **반영된 파일**: 어느 파일에 업데이트됐는가
```

---

## 파일 작성 규칙

### 파일명
```
{접두사}.{설명}.md

✅ PRD.기능정의.md
✅ SPEC.영상처리플로우.md
✅ ARCH.모듈구조결정.md
✅ REF.FFmpeg명령어모음.md
✅ DONE.초기기획_v1.md

❌ 기획서.md              (접두사 없음)
❌ PRD_기능정의.md        (언더스코어 구분자)
❌ PRD.기능정의.20240101.md (파일명에 날짜)
```

### 파일 내 날짜 표기
```markdown
# PRD.기능정의.md

> 최종 수정: 2024-01-15

## 기능 목록
...
```

날짜는 파일명이 아닌 **파일 내 메타데이터**로 관리한다.

### DONE 처리 규칙
```
더 이상 유효하지 않은 문서는 파일명 앞에 DONE. 을 붙인다.
삭제하지 않고 DONE. 으로 보존 → 히스토리 유지
Claude는 항상 스킵
```

---

## 토큰 절약 효과 (예시)

```
프로젝트 Vault 파일 10개 가정:
PRD.기능정의.md         3,000 토큰
PRD.사용자시나리오.md    2,000 토큰
SPEC.영상처리.md        4,000 토큰
SPEC.API설계.md         3,500 토큰
ARCH.모듈구조.md        2,500 토큰
REVIEW.md              1,000 토큰 (미결만 500)
REF.FFmpeg.md          5,000 토큰
REF.CrewAI가이드.md     6,000 토큰
DONE.초기기획.md        4,000 토큰
DONE.구버전스펙.md      3,000 토큰

전체 읽기: 34,000 토큰
─────────────────────────
Architect 읽기:
  PRD(2) + SPEC(2) + ARCH(1) + REVIEW 미결(0.5)
  = 15,000 토큰  (56% 절약)

Backend Engineer 읽기:
  SPEC(2) + ARCH(1) + REVIEW 미결(0.5)
  = 10,000 토큰  (71% 절약)
```

---

## Claude 읽기 순서 원칙

같은 접두사 파일이 여러 개일 때 읽는 순서:

```
1. ARCH.* 먼저   — 아키텍처 결정이 모든 판단의 기준
2. SPEC.*        — 기술 스펙
3. PRD.*         — 기획 요구사항
4. REVIEW.md     — 미결 사항 (마지막에 읽어 최신 변경 반영)
```

REVIEW.md를 마지막에 읽는 이유:
앞서 읽은 문서의 내용 중 "아직 확정되지 않은 부분"을 REVIEW.md로 보정하기 위함.

---

## 금지 패턴

```
❌ Vault 폴더 전체 파일 무분별하게 읽기
❌ DONE.* 파일 읽기
❌ REF.* 파일을 기본으로 읽기 (명시적 요청 시에만)
❌ REVIEW.md 완료 섹션 읽기
❌ 파일명에 날짜 포함 (SPEC.API설계.20240101.md)
❌ 접두사 없는 파일 생성 (기획서.md, 메모.md)
```
