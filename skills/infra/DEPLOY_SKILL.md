---
name: vibe-framework-deploy
description: >
  Vibe Framework의 배포 전략.
  OCI(Oracle Cloud Infrastructure) VM에서 Docker Compose로 운영.
  GitHub Actions로 CI/CD 파이프라인 구성.
  "배포해줘", "CI/CD 설정해줘", "서버 구성해줘" 등의 요청 시 참조.
---

# Deploy Skill

## 핵심 원칙

1. **로컬과 서버의 실행 방식이 같다** — Docker Compose 기반. 쿠버네티스 불필요
2. **main 브랜치가 배포의 기준이다** — main에 merge 되면 자동 배포
3. **비밀값은 GitHub Secrets + .env로 관리** — 코드에 절대 포함 금지
4. **롤백은 이전 이미지 태그로** — 항상 이미지에 태그를 붙여 추적 가능하게
5. **배포 전 테스트 통과 필수** — CI에서 테스트 실패 시 배포 차단

---

## 인프라 구성

### OCI VM 스펙 (권장)

```
인스턴스: VM.Standard.A1.Flex (ARM) — Always Free 대상
  CPU: 4 OCPU
  RAM: 24GB
  디스크: 100GB (Block Volume)
  OS: Ubuntu 22.04 (aarch64)

네트워크:
  VCN + Public Subnet
  Security List:
    - 22    (SSH)
    - 80    (HTTP → Nginx → Backend)
    - 443   (HTTPS → Nginx → Backend)
    - 3000  (Grafana — 개발 중만. 운영 시 닫거나 VPN 뒤로)
```

ARM 인스턴스이므로 Docker 이미지 빌드 시 `linux/arm64` 플랫폼 지정 필수.

### VM 초기 설정

```bash
# 1. Docker 설치
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER

# 2. 프로젝트 디렉토리 생성
sudo mkdir -p /opt/vibe
sudo chown $USER:$USER /opt/vibe

# 3. .env 파일 생성 (GitHub Secrets에서 가져온 값으로)
# .env는 절대 Git에 포함되지 않음. 서버에서만 존재.
cp .env.example /opt/vibe/.env
# 각 값을 실제 값으로 채움

# 4. Nginx 설치 (리버스 프록시)
sudo apt install -y nginx certbot python3-certbot-nginx
```

### Nginx 리버스 프록시

```nginx
# /etc/nginx/sites-available/vibe
server {
    listen 80;
    server_name {your-domain.com};

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 파일 업로드 크기 제한
        client_max_body_size 100M;
    }

    # Supabase (Flutter에서 직접 접근)
    location /supabase/ {
        proxy_pass http://localhost:8001/;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

```bash
# HTTPS 설정 (Let's Encrypt)
sudo certbot --nginx -d {your-domain.com}
```

---

## GitHub Actions CI/CD 파이프라인

### 디렉토리 구조

```
.github/
└── workflows/
    ├── ci.yml         # PR 시 테스트만 실행
    └── deploy.yml     # main merge 시 빌드 + 배포
```

### ci.yml — Pull Request 테스트

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5

    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      
      - name: Install dependencies
        run: |
          pip install -r backend/requirements.txt
          pip install -r tests/requirements.txt
      
      - name: Run unit tests
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test
        run: pytest tests/unit/ -v --cov
      
      - name: Set up Flutter
        uses: subosito/flutter-action@v2
        with:
          flutter-version: "3.22.0"
      
      - name: Run Flutter tests
        working-directory: mobile
        run: flutter test
```

### deploy.yml — 메인 브랜치 배포

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  push:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ${{ github.repository }}

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r backend/requirements.txt -r tests/requirements.txt
      - run: pytest tests/unit/ -v
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test

  build-and-push:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    
    strategy:
      matrix:
        service: [backend, crewai, workers]
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up QEMU (ARM 크로스 빌드용)
        uses: docker/setup-qemu-action@v3
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Login to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: ./${{ matrix.service }}
          push: true
          platforms: linux/arm64
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_PREFIX }}/${{ matrix.service }}:latest
            ${{ env.REGISTRY }}/${{ env.IMAGE_PREFIX }}/${{ matrix.service }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy-staging:
    needs: build-and-push
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Deploy to Staging
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.OCI_STAGING_HOST }}
          username: ${{ secrets.OCI_USER }}
          key: ${{ secrets.OCI_SSH_KEY }}
          script: |
            cd /opt/vibe-staging
            
            git pull origin main
            
            echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            docker compose -f docker-compose.yml -f docker-compose.staging.yml pull
            docker compose -f docker-compose.yml -f docker-compose.staging.yml --env-file .env.staging up -d --remove-orphans
            
            sleep 10
            curl -f http://localhost:8000/health || exit 1
            
            docker image prune -f
            echo "Staging deploy complete: ${{ github.sha }}"

  deploy-prod:
    needs: deploy-staging
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Deploy to Production
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.OCI_PROD_HOST }}
          username: ${{ secrets.OCI_USER }}
          key: ${{ secrets.OCI_SSH_KEY }}
          script: |
            cd /opt/vibe-prod
            
            git pull origin main
            
            echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
            docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d --remove-orphans
            
            sleep 10
            curl -f http://localhost:8000/health || exit 1
            
            docker image prune -f
            echo "Production deploy complete: ${{ github.sha }}"
```

---

## GitHub Secrets 설정

```
필수 Secrets (GitHub Repository → Settings → Secrets):

OCI_STAGING_HOST    # staging VM의 Public IP
OCI_PROD_HOST       # prod VM의 Public IP (같은 VM이면 동일 IP)
OCI_USER            # SSH 사용자명 (ubuntu)
OCI_SSH_KEY         # SSH 개인키 (PEM 형식)

GITHUB_TOKEN은 자동 제공됨 (ghcr.io 로그인용)
```

> staging과 prod가 같은 VM이면 OCI_STAGING_HOST = OCI_PROD_HOST.
> 디렉토리(`/opt/vibe-staging/`, `/opt/vibe-prod/`)로 분리.

---

## 환경변수 관리 전략

> 환경 분리 상세 규칙은 `structure/DOCKER_SKILL.md` "환경 분리" 섹션 참조.

```
■ 3환경 체계:
  dev      → 로컬 개발 (.env.dev, docker-compose.dev.yml)
  staging  → 통합 테스트/QA (.env.staging, docker-compose.staging.yml)
  prod     → 운영 (.env.prod, docker-compose.prod.yml)

■ .env 파일 관리:
  .env.example → Git에 커밋 (플레이스홀더만)
  .env.dev     → 각 개발자 로컬에만 존재
  .env.staging → staging VM /opt/vibe/ 에만 존재
  .env.prod    → prod VM /opt/vibe/ 에만 존재

■ 환경 구분 변수:
  VIBE_ENV=dev|staging|prod
  코드에서 이 값으로 환경을 판별한다.

■ CI (GitHub Actions):
  테스트용 값은 ci.yml의 env에 인라인 정의
  실제 Secrets는 배포 시에만 사용

■ 새 환경변수 추가 시:
  1. .env.example에 플레이스홀더 추가 (Git 커밋)
  2. .env.dev / .env.staging / .env.prod 각각에 실제 값 설정
  3. CONTEXT.md에 변수 목록 업데이트
```

---

## 배포 프로세스 요약

```
개발자 (로컬 — dev)
  │
  ├── feature 브랜치에서 개발
  ├── PR 생성 → ci.yml 실행 (테스트)
  ├── 테스트 통과 → main에 merge
  │
  ▼
GitHub Actions (deploy.yml)
  │
  ├── 단위 테스트 실행 → 실패 시 중단
  ├── Docker 이미지 빌드 (linux/arm64)
  ├── ghcr.io에 이미지 push (latest + SHA 태그)
  │
  ├── [1] staging 배포
  │   ├── staging VM에 SSH 접속
  │   ├── docker compose pull + up -d (staging.yml)
  │   ├── 헬스체크 + 통합 테스트 실행
  │   └── 실패 시 → 중단, prod 배포 안 함
  │
  ├── [2] prod 배포 (staging 통과 시)
  │   ├── prod VM에 SSH 접속
  │   ├── docker compose pull + up -d (prod.yml)
  │   ├── 헬스체크 확인
  │   └── 완료
  │
  └── 알림 (성공/실패)
```

> staging과 prod가 같은 VM이라면, 별도 디렉토리로 분리:
> `/opt/vibe-staging/` + `/opt/vibe-prod/`

---

## 롤백 규칙

```
■ 즉시 롤백이 필요한 경우:
  1. 배포 후 /health 실패
  2. Grafana에서 에러 로그 급증
  3. 사용자 리포트

■ 롤백 방법 (이미지 태그 기반):
  # 이전 커밋 SHA로 롤백
  ssh {OCI_HOST}
  cd /opt/vibe
  
  # docker-compose.yml에서 이미지 태그를 이전 SHA로 변경
  # 또는:
  docker compose pull   # 이전 이미지가 캐시에 있으면
  docker compose up -d
  
  # 확인
  curl -f http://localhost:8000/health

■ 롤백 후:
  - 에러 원인 분석 → QA Engineer (모드 A) 투입
  - TIL 작성
  - 수정 후 다시 main에 merge → 자동 배포
```

---

## docker-compose.prod.yml (운영 오버라이드)

```yaml
# docker-compose.prod.yml
# 운영 환경 전용 설정. 기본 docker-compose.yml 위에 오버라이드.
# 사용: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

services:
  backend:
    image: ghcr.io/{owner}/{repo}/backend:latest
    build: !reset null                    # 로컬 빌드 비활성화
    restart: always
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  crewai:
    image: ghcr.io/{owner}/{repo}/crewai:latest
    build: !reset null
    restart: always
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  postgres:
    restart: always
    volumes:
      - postgres_data:/var/lib/postgresql/data    # 영속 볼륨

  redis:
    restart: always

  kafka:
    restart: always

  minio:
    restart: always
    volumes:
      - minio_data:/data                          # 영속 볼륨

  grafana:
    # 운영 시 외부 접근 제한 (포트 바인딩 제거, Nginx 뒤에서만)
    ports: !reset []

volumes:
  postgres_data:
  minio_data:
```

---

## Dockerfile 멀티 플랫폼 빌드 규칙

```dockerfile
# ARM64 호환을 위한 Dockerfile 작성 규칙:
# 1. 베이스 이미지는 멀티 아키텍처 지원하는 것 사용
#    ✅ python:3.11-slim (arm64 지원)
#    ❌ 특정 아키텍처 전용 이미지

# 2. 네이티브 의존성이 있으면 빌드 스테이지에서 컴파일
FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y gcc
COPY requirements.txt .
RUN pip wheel --no-deps -w /wheels -r requirements.txt

FROM python:3.11-slim
COPY --from=builder /wheels /wheels
RUN pip install /wheels/*
```

---

## 모니터링 (운영)

```
■ 헬스체크:
  - Nginx → Backend /health 주기적 확인
  - docker compose에서 각 서비스 healthcheck (이미 설정됨)

■ 로그:
  - Grafana (이미 설정됨) — 개발/운영 동일
  - 운영 시 Grafana 외부 접근 차단 (SSH 터널 또는 VPN으로만)

■ 디스크:
  - Docker 이미지/볼륨이 디스크를 채우지 않도록
  - 배포 시 docker image prune -f 실행 (deploy.yml에 포함됨)
  - MinIO 파일 보관 정책 설정 (lifecycle rule)

■ 알림 (선택):
  - GitHub Actions 실패 시 → Slack/Discord 알림
  - Grafana Alert → 에러 로그 임계치 초과 시 알림
```

---

## 금지 패턴

```
❌ .env 파일을 Git에 커밋
❌ 비밀값을 docker-compose.yml이나 코드에 하드코딩
❌ main에 직접 push (PR을 통해서만)
❌ 테스트 실패 상태에서 배포 진행
❌ 서버에서 직접 코드 수정 (항상 Git을 통해)
❌ docker compose build를 서버에서 실행 (이미지는 CI에서 빌드)
❌ 롤백 없이 에러 상태 방치
❌ ARM 비호환 이미지 사용 (linux/arm64 플랫폼 지정 필수)
```
