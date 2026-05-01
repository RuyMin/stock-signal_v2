# Vibe Framework

이 프로젝트는 **Vibe Framework** 기반으로 개발된다.
모든 코드 생성, 구조 설계, 에러 처리는 아래 문서를 따른다.

## 개발 환경

> 아래 설정은 `.vibe/config.yml`의 `environment` 섹션과 일치시킨다.
> 프로젝트 세팅 시 본인 환경에 맞게 이 섹션을 수정할 것.

```
OS: Windows (WSL2)
Shell: bash (WSL2)
프로젝트 루트: /mnt/f/projects/reels-generator
Obsidian Vault: /mnt/d/Obsidian/MyVault
```

**이 환경에서의 규칙:**
- 모든 경로는 WSL2 형식(`/mnt/드라이브/...`)으로 사용한다. Windows 네이티브 경로(`F:\...`) 사용 금지
- 서비스 시작: `sudo service docker start` (`systemctl` 사용 불가)
- 파일 권한: WSL2에서 생성한 파일은 chmod가 제대로 동작하지 않을 수 있으므로, 실행 권한이 필요한 스크립트(entrypoint.sh 등)는 Dockerfile 안에서 `RUN chmod +x`로 처리
- 줄바꿈: Git 설정에서 `core.autocrlf=input` 확인 (CRLF → LF 자동 변환)
- Docker 볼륨 마운트 시 `/mnt/` 경로의 I/O가 느릴 수 있음. 빌드 컨텍스트는 WSL2 파일시스템(`~/projects/...`)에 두는 것을 권장

<!--
macOS 사용 시 아래로 교체:
OS: macOS
Shell: zsh
프로젝트 루트: /Users/username/projects/reels-generator
Obsidian Vault: /Users/username/Obsidian/MyVault
규칙: 경로는 macOS 표준. Docker Desktop 사용. chmod 정상 동작.

Linux 사용 시 아래로 교체:
OS: Linux
Shell: bash
프로젝트 루트: /home/username/projects/reels-generator
Obsidian Vault: /home/username/Obsidian/MyVault
규칙: 경로는 Linux 표준. systemctl로 서비스 관리. chmod 정상 동작.
-->

## 필수 읽기

작업 시작 전 반드시 아래 파일을 읽는다:

1. `skills/MASTER_SKILL.md` — 전체 아키텍처, 스택, 모듈 간 통신 규칙
2. `skills/personas/FLOW_GUIDE.md` — 페르소나 호출 순서, 입출력 체인
3. `skills/START_GUIDE.md` — 프로젝트 시작/이어받기 방법

## 페르소나 시스템

이 프레임워크는 페르소나 기반으로 동작한다. 각 페르소나는 `skills/personas/` 에 정의되어 있다.
`/architect`, `/backend` 등 슬래시 커맨드로 호출할 수 있다.

## 핵심 규칙 (항상 적용)

- **CONTEXT.md가 프로젝트의 중심이다** — 작업 시작 전 읽고, 완료 후 업데이트
- **모듈 경계 절대 준수** — FastAPI에서 AI 처리 금지, Flutter에서 직접 DB 접근 금지
- **shared/schemas/가 인터페이스 계약** — 임의 변경 금지
- **모든 로그는 structlog JSON + job_id** — print() 절대 금지
- **에러는 Kafka DLQ로** — 재시도 가능한 작업은 Kafka 경유
- **인증은 Supabase Auth 위임** — Backend는 JWT 검증만

## SKILL 파일 구조

```
skills/
├── MASTER_SKILL.md          # 전체 아키텍처
├── START_GUIDE.md           # 시작 가이드
├── OBSIDIAN_SKILL.md        # Vault 읽기 규칙
├── personas/                # 페르소나 정의
├── backend/                 # FastAPI SKILL
├── mobile/                  # Flutter SKILL
├── crewai/                  # CrewAI SKILL
├── infra/                   # Kafka, MinIO, Supabase, Deploy, Performance, Logging
├── structure/               # 디렉토리, Docker SKILL
├── shared/                  # API Contract SKILL
├── context/                 # 템플릿 (CONTEXT, TEST_SPEC, ERROR_LOG, TIL, RETRO, REVIEW)
└── testing/                 # 테스트 SKILL
```

각 페르소나가 어떤 SKILL을 읽어야 하는지는 `MASTER_SKILL.md` 하단의 "하위 SKILL 참조 가이드" 표를 따른다.
