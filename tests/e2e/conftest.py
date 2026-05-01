"""E2E 전용 fixture — 통합 conftest 의존성 그대로 가져옴."""
from tests.integration.conftest import (  # noqa: F401
    BACKEND_BASE_URL,
    DEV_DB_DSN,
    KAFKA_BOOTSTRAP,
    _docker_stack_up,
    _require_docker,
    dev_pool,
    kafka_consumer_factory,
    kafka_producer,
    wait_for,
    wait_for_fn,
)
