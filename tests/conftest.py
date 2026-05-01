"""공통 fixture — TEST_SKILL.md 패턴 준수.

전제:
- docker compose up -d 로 postgres / kafka 가 떠 있음 (호스트에서 localhost로 접근)
- pytest 실행 전 .env.test 로드 (또는 PYTHONPATH + 환경변수 주입)
- 테스트 DB는 별도(stock_signal_test) — 자동 생성/리셋
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio


# ─── 환경변수 주입 (pytest 시작 시점) ──────────────────────────────

def _pg_host() -> str:
    """컨테이너 안에서 실행 시 'postgres', 호스트에서는 'localhost'."""
    return os.environ.get("POSTGRES_HOST", "localhost")


def _kafka_bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def pytest_configure(config):
    os.environ.setdefault("VIBE_ENV", "dev")
    os.environ.setdefault(
        "DATABASE_URL",
        f"postgresql+asyncpg://stock:codedream@{_pg_host()}:5432/stock_signal_test",
    )
    os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", _kafka_bootstrap())
    os.environ.setdefault("BACKEND_LOG_LEVEL", "WARNING")  # 테스트 중 로그 시끄러움 방지
    os.environ.setdefault("CORS_ORIGINS", "*")
    os.environ.setdefault("KIS_APP_KEY", "test")
    os.environ.setdefault("KIS_APP_SECRET", "test")
    os.environ.setdefault("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    os.environ.setdefault("OPENAI_MODEL_NAME", "gpt-4o-mini")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
    os.environ.setdefault("TELEGRAM_AUTHORIZED_CHAT_ID", "12345")
    os.environ.setdefault("BACKEND_URL", "http://localhost:8000")


# ─── 이벤트 루프 (session scope) ──────────────────────────────────


@pytest.fixture(scope="session")
def event_loop():
    """session scope 이벤트 루프 — 모든 async test 공유."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ─── 테스트 DB 세팅 / 리셋 ──────────────────────────────────────


def _admin_dsn() -> str:
    """admin 접속 (postgres DB로 접속해서 stock_signal_test 생성/삭제)."""
    pw = os.environ.get("POSTGRES_PASSWORD", "codedream")
    user = os.environ.get("POSTGRES_USER", "stock")
    return f"postgresql://{user}:{pw}@{_pg_host()}:5432/postgres"


def _test_dsn() -> str:
    return f"postgresql://stock:codedream@{_pg_host()}:5432/stock_signal_test"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_test_db():
    """세션 시작 시 stock_signal_test DB 생성 + init.sql 적용. 종료 시 drop."""
    admin = await asyncpg.connect(_admin_dsn())
    await admin.execute("DROP DATABASE IF EXISTS stock_signal_test")
    await admin.execute("CREATE DATABASE stock_signal_test")
    await admin.close()

    # init.sql 적용
    test_conn = await asyncpg.connect(_test_dsn())
    init_sql_path = os.path.join(
        os.path.dirname(__file__), "..", "infra", "postgres", "init.sql"
    )
    with open(init_sql_path, "r", encoding="utf-8") as f:
        await test_conn.execute(f.read())
    await test_conn.close()

    yield

    # cleanup
    admin = await asyncpg.connect(_admin_dsn())
    await admin.execute("DROP DATABASE IF EXISTS stock_signal_test")
    await admin.close()


@pytest_asyncio.fixture
async def db_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """각 테스트마다 pool 생성. 테스트 끝나면 모든 도메인 테이블 truncate.

    테스트가 의도적으로 pool을 닫은 경우(예: WRK-E005/E009 PG 연결 실패 시뮬레이션)
    truncate는 별도 connection으로 수행한다.
    """
    pool = await asyncpg.create_pool(_test_dsn(), min_size=1, max_size=3)
    try:
        yield pool
    finally:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "TRUNCATE jobs, job_errors, holdings, users, signals, news, "
                    "macro_indicators, recommendations RESTART IDENTITY CASCADE"
                )
            await pool.close()
        except (asyncpg.InterfaceError, RuntimeError):
            # pool이 이미 테스트에 의해 닫힘 — 별도 conn으로 truncate
            conn = await asyncpg.connect(_test_dsn())
            try:
                await conn.execute(
                    "TRUNCATE jobs, job_errors, holdings, users, signals, news, "
                    "macro_indicators, recommendations RESTART IDENTITY CASCADE"
                )
            finally:
                await conn.close()


# ─── Backend FastAPI ASGI client ─────────────────────────────────


@pytest_asyncio.fixture
async def api_client(db_pool):
    """httpx ASGI 클라이언트 — backend.main:app 직접 호출."""
    import sys

    backend_path = os.path.join(os.path.dirname(__file__), "..", "backend")
    schemas_path = os.path.join(os.path.dirname(__file__), "..", "shared")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    if schemas_path not in sys.path:
        sys.path.insert(0, schemas_path)

    from httpx import ASGITransport, AsyncClient
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ─── 공통 데이터 ─────────────────────────────────────────────────


@pytest.fixture
def sample_job_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_ticker() -> str:
    return "005930"
