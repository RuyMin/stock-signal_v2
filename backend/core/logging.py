"""structlog 설정 — JSON 출력 + job_id Correlation."""
import logging
import sys

import structlog


def setup_logging(service_name: str, level: str = "DEBUG") -> None:
    """모든 진입점에서 1회 호출."""
    log_level = getattr(logging, level.upper(), logging.DEBUG)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


logger = structlog.get_logger()
