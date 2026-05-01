#!/bin/bash
set -e

# 첫 부팅: postgres init.sql이 이미 모든 테이블을 생성함.
# alembic_version이 비어있으면 stamp head로 baseline 표시.
# 그 외에는 upgrade head로 차이분 적용.
echo "[entrypoint] Running Alembic migrations..."

if alembic current 2>/dev/null | grep -qE '\(head\)|^$'; then
    # 빈 출력이면 alembic_version 테이블이 비어있음 → stamp
    alembic stamp head || true
fi

alembic upgrade head

echo "[entrypoint] Starting FastAPI..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
