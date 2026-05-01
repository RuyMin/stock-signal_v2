# RUNBOOK — stock-signal

> 첫 부팅 / 운영 / 검증 / 롤백 절차. DevOps Engineer 단계 산출물.
> 본 문서는 **사람이 직접 실행**하는 절차를 담는다 (CI/CD가 아닌 부분).

---

## 1. 사전 준비

### 1.1 외부 키 발급
| 항목 | 발급 위치 | 비고 |
|------|---------|------|
| `OPENAI_API_KEY` | https://platform.openai.com | 결제 한도 월 USD 5 이하 권장 |
| `KIS_APP_KEY` / `KIS_APP_SECRET` | KIS Developers (https://apiportal.koreainvestment.com) | 개인용 모의/실전 모두 가능 |
| `TELEGRAM_BOT_TOKEN` | @BotFather (텔레그램) | `/newbot` 후 토큰 복사 |
| `TELEGRAM_AUTHORIZED_CHAT_ID` | @userinfobot 또는 봇 첫 대화 후 `getUpdates` API | 본인 chat_id (정수) |

### 1.2 GitHub 저장소
- 저장소 생성 후 `.env.example` 기준으로 환경별 `.env` 생성:
  - `.env.dev` (로컬)
  - `.env.staging` (OCI VM `/opt/vibe-staging/`)
  - `.env.prod` (OCI VM `/opt/vibe-prod/`)
- **셋 다 Git 커밋 금지.**
- GitHub Secrets에 등록 (배포 시점):
  - `OCI_HOST` / `OCI_USER` / `OCI_SSH_KEY`
- GitHub Repository Variables (선택):
  - `STAGING_ENABLED=true` (staging 배포를 활성화하려면)

---

## 2. 로컬 첫 부팅 (개발)

```bash
# 1) .env 작성
cp .env.example .env.dev
# .env.dev 의 모든 CHANGE_ME 를 실제 값으로 채움
#   - GHCR_IMAGE_PREFIX 는 dev에서는 사용 안 함 (build 모드)
#   - 단, IMAGE_TAG 는 latest 유지

# 2) 빌드 + 기동 (12개 컨테이너)
docker compose -f docker-compose.yml -f docker-compose.dev.yml --env-file .env.dev up -d --build

# 3) 헬스체크
docker compose ps
curl -fsS http://localhost:8000/health    # {"status":"ok"}

# 4) Grafana
open http://localhost:3000
# admin / GRAFANA_ADMIN_PASSWORD 값
# Dashboards > stock-signal > "Job 흐름 추적" 자동 provisioning 확인

# 5) Kafka 토픽 확인 (자동 생성)
docker compose exec kafka kafka-topics --bootstrap-server kafka:9092 --list

# 6) PostgreSQL 스키마 확인
docker compose exec postgres psql -U stock -d stock_signal_dev -c "\dt"
# 7개 테이블: jobs, job_errors, holdings, signals, news, macro_indicators, recommendations
```

---

## 3. 헬스체크 (Smoke Test)

```bash
# 3.1 모든 서비스 healthy?
docker compose ps --format json | grep -E '"Health":\s*"healthy"' | wc -l
# 기대: postgres, kafka, loki, backend (4개 중 healthcheck 정의된 것 모두 healthy)

# 3.2 Backend 엔드포인트
curl http://localhost:8000/health                   # 200
curl http://localhost:8000/holdings                 # 200, items: []
curl http://localhost:8000/recommendations/recent   # 200, items: []

# 3.3 텔레그램 봇 (사용자 직접)
# 봇과 대화: /start → 환영 메시지 수신
# /add 005930 → "추가됨: 005930" (name은 worker 첫 사이클에서 채움)
# /list → 보유 목록

# 3.4 수동 트리거 (scheduler 안 기다리고 즉시 테스트)
docker compose exec kafka kafka-console-producer \
  --bootstrap-server kafka:9092 \
  --topic stock.data.requested
# 입력: {"job_id":"manual-test-001","target_date":"2026-04-29","triggered_at":"..."}
# 종료: Ctrl+C

# 3.5 Grafana에서 추적
# job_id 변수에 "manual-test-001" 입력 → 전체 흐름 로그 확인
# scheduler → worker-data-collector → crewai → worker-telegram-notifier 순으로 service 라벨 확인
```

---

## 4. OCI VM 초기 셋업 (스테이징/운영)

```bash
# 4.1 VM 인스턴스 생성
# - VM.Standard.A1.Flex (ARM Ampere) — Always Free
# - 4 OCPU / 24GB RAM / 100GB Block Volume
# - Ubuntu 22.04 (aarch64)

# 4.2 SSH 접속 후 Docker 설치
# Ubuntu 22.04 기본 apt repo에는 docker-compose-plugin이 없음 → Docker 공식 repo 추가 필요.
ssh ubuntu@$OCI_HOST

# (1) 충돌 가능한 기존 패키지 제거
sudo apt remove -y docker.io docker-doc docker-compose podman-docker containerd runc 2>/dev/null || true

# (2) Docker 공식 GPG 키 + repo 추가
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# (3) Docker CE + plugin 설치
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# (4) 그룹 적용 후 재접속
sudo usermod -aG docker $USER
exit && ssh ubuntu@$OCI_HOST

# (5) 검증
docker --version
docker compose version

# 4.3 디렉토리 생성
sudo mkdir -p /opt/vibe-staging /opt/vibe-prod
sudo chown $USER:$USER /opt/vibe-staging /opt/vibe-prod

# 4.4 코드 clone (staging/prod 각각)
cd /opt/vibe-staging
git clone https://github.com/<owner>/stock-signal.git .

cd /opt/vibe-prod
git clone https://github.com/<owner>/stock-signal.git .

# 4.5 .env 파일 배치 (로컬에서 scp로 전송, Git에는 절대 안 들어감)
# 로컬에서:
scp .env.staging ubuntu@$OCI_HOST:/opt/vibe-staging/.env.staging
scp .env.prod    ubuntu@$OCI_HOST:/opt/vibe-prod/.env.prod

# 4.6 GHCR 로그인 (1회)
echo $GITHUB_TOKEN | docker login ghcr.io -u $GITHUB_USER --password-stdin

# 4.7 첫 기동 (수동 — 이후는 GitHub Actions가 자동)
cd /opt/vibe-prod
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d
```

---

## 5. 운영 검증

```bash
# 5.1 매일 15:35 KST 자동 트리거 확인
docker compose logs scheduler --since 1h | grep scheduler_triggered

# 5.2 데이터 수집 검증
docker compose exec postgres psql -U stock -d stock_signal_prod -c "
  SELECT date, COUNT(*) FROM signals GROUP BY date ORDER BY date DESC LIMIT 5;
"

# 5.3 추천 결과 검증
docker compose exec postgres psql -U stock -d stock_signal_prod -c "
  SELECT date, recommendation_type, COUNT(*), AVG(score)
  FROM recommendations GROUP BY date, recommendation_type
  ORDER BY date DESC LIMIT 10;
"

# 5.4 텔레그램 송신 확인
# 사용자 텔레그램에서 매일 15:35 직후 메시지 수신 여부

# 5.5 Grafana 대시보드
# - 서비스별 로그 발생량 그래프 정상
# - 1시간 ERROR 0건 (에러 발생 시 알림 설정 추가 권장)
```

---

## 6. 롤백

```bash
# 6.1 직전 SHA로 롤백
ssh ubuntu@$OCI_HOST
cd /opt/vibe-prod

# 직전 SHA 확인 (git log)
PREV_SHA=$(git log --format=%H -n 2 main | tail -1)

# .env.prod의 IMAGE_TAG를 직전 SHA로 변경
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$PREV_SHA/" .env.prod

# 재기동
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d

# 헬스체크
sleep 10
curl -fsS http://localhost:8000/health
```

---

## 7. 알려진 운영 이슈

### KIS API endpoint stub
- `workers/data_collector/clients/kis_api.py` — `fetch_signals` / `fetch_ticker_name`이 빈 결과 반환
- 결과: signals 테이블 빈 상태 → CrewAI가 "조건 충족 종목 없음" 추천 → 텔레그램에 동일 메시지
- 해결: KIS Developers 공식 문서로 endpoint/TR_ID 확정 → AI Engineer/Backend Engineer 개입

### 네이버 셀렉터
- `workers/data_collector/clients/naver_scraper.py` — `a.tit, td.title a`는 임시값
- 차단(429/403) 시 자동 스킵하므로 작업은 계속 진행하되, 셀렉터가 맞지 않으면 뉴스 0건
- 해결: 실제 페이지 HTML 검증

### CrewAI 모델 변수
- `OPENAI_MODEL_NAME=gpt-4o-mini`로 설정 — CrewAI/LiteLLM이 인식 (일반적)
- 호출 후 비용이 예상보다 높으면 BaseAgent에 `llm` 명시 필요 (AI Engineer 영역)

### 휴장일 처리
- `holidays.KR()`가 한국 공휴일 + 주말만 처리
- KRX 임시 휴장(예: 마지막 거래일이 공휴일이라 직전일이 휴장)은 미반영 가능
- 운영 1주 후 보정

---

## 8. 일상 운영 체크리스트

| 주기 | 항목 |
|------|------|
| 매일 (자동) | scheduler trigger 발화 (15:35 KST) → 텔레그램 알림 수신 |
| 주 1회 | Grafana 에러 로그 점검 |
| 월 1회 | OpenAI API 사용량 / 비용 점검 (월 USD 3 이내) |
| 월 1회 | yfinance / 네이버 페이지 구조 변경 여부 점검 |
| 분기 1회 | 추천 정확도 사후 검증 (score 분포 vs 실제 수익률) |
| 연 1회 | KRX 임시 휴장일 캘린더 보정 |

---

## 9. 비상 연락 / 에스컬레이션

- 에러 급증 → Grafana ERROR 로그 → CONTEXT.md "최근 에러 이력"에 기록 → QA Engineer 모드 A 투입
- KIS API 인증 실패 → 토큰 갱신 + .env 키 확인
- 텔레그램 송신 실패 → 봇 토큰 / chat_id 검증
