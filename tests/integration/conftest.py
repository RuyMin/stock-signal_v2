"""통합 테스트용 공통 fixture.

전제: docker compose -f docker-compose.yml -f docker-compose.dev.yml --env-file .env.dev up -d
모든 서비스(backend / kafka / postgres / crewai / workers / scheduler)가 healthy 상태여야 함.

dev 환경이 사용 중인 stock_signal_dev DB를 그대로 사용 — 통합 테스트는 실제 동작 검증이 목적.
테스트마다 도메인 테이블 truncate.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncGenerator
from typing import Awaitable, Callable

import asyncpg
import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

# pytest_configure는 부모 conftest에서 환경변수 세팅
# 통합은 dev DB(stock_signal_dev) 사용하도록 override

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DEV_DB_DSN = "postgresql://stock:codedream@localhost:5432/stock_signal_dev"
BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")


# ─── Docker 헬스 체크 (테스트 시작 전) ─────────────────────────────


def _service_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _docker_stack_up() -> bool:
    """필수 서비스 포트가 떠 있는지."""
    return all(
        _service_reachable(*addr)
        for addr in [("localhost", 5432), ("localhost", 9092), ("localhost", 8000)]
    )


@pytest.fixture(scope="session", autouse=True)
def _require_docker():
    """Docker 스택이 안 떠 있으면 통합 테스트 전체 skip."""
    if not _docker_stack_up():
        pytest.skip(
            "Docker stack not running. "
            "Start with: docker compose -f docker-compose.yml -f docker-compose.dev.yml "
            "--env-file .env.dev up -d",
            allow_module_level=True,
        )


# ─── DB pool (dev DB 직접 사용) ────────────────────────────────────


@pytest_asyncio.fixture
async def dev_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    pool = await asyncpg.create_pool(DEV_DB_DSN, min_size=1, max_size=3)
    yield pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE jobs, job_errors, holdings, signals, news, "
            "macro_indicators, recommendations RESTART IDENTITY CASCADE"
        )
    await pool.close()


# ─── Kafka producer/consumer ──────────────────────────────────────


@pytest_asyncio.fixture
async def kafka_producer() -> AsyncGenerator[AIOKafkaProducer, None]:
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
    )
    await producer.start()
    yield producer
    await producer.stop()


@pytest_asyncio.fixture
async def kafka_consumer_factory():
    """topic + group_id 받아 컨슈머 만들고 yield. 테스트 종료 시 모두 stop."""
    consumers: list[AIOKafkaConsumer] = []

    async def _make(topic: str, group_id: str | None = None) -> AIOKafkaConsumer:
        c = AIOKafkaConsumer(
            topic,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=group_id or f"test-{topic}-{os.urandom(4).hex()}",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="latest",  # 통합테스트는 새 메시지만
            enable_auto_commit=False,
        )
        await c.start()
        consumers.append(c)
        return c

    yield _make

    for c in consumers:
        await c.stop()


# ─── 폴링 헬퍼 ─────────────────────────────────────────────────────


async def wait_for(
    predicate: Callable[[], Awaitable[bool] | bool],
    timeout: float = 30.0,
    interval: float = 0.5,
    description: str = "",
) -> None:
    """predicate가 True 될 때까지 폴링. 미달 시 AssertionError."""
    elapsed = 0.0
    while elapsed < timeout:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)
        elapsed += interval
    raise AssertionError(f"timeout {timeout}s — {description}")


@pytest.fixture
def wait_for_fn():
    return wait_for
