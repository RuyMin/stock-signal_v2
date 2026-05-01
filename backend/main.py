"""FastAPI 진입점.

stock-signal Backend 게이트웨이:
- holdings CRUD (단일 사용자 — 인증 불필요)
- recommendations 조회
- jobs 상태 조회
- health
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import settings
from core.dependencies import ResponseHeadersMiddleware
from core.exceptions import (
    VibeException,
    http_exception_handler,
    integrity_error_handler,
    validation_exception_handler,
    vibe_exception_handler,
)
from core.logging import setup_logging
from routers import health, holdings, jobs, recommendations, users

setup_logging(service_name="backend", level=settings.BACKEND_LOG_LEVEL)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup_complete", env=settings.VIBE_ENV.value)
    yield
    logger.info("shutdown_complete")


app = FastAPI(
    title="stock-signal Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 단일 사용자 도구지만 표준 적용
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 공통 응답 헤더 미들웨어 — 순수 ASGI 미들웨어로 적용 (BaseHTTPMiddleware의
# anyio TaskGroup + 예외 핸들러 충돌 회피)
app.add_middleware(ResponseHeadersMiddleware)

# 예외 핸들러
app.add_exception_handler(VibeException, vibe_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(IntegrityError, integrity_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]

# 라우터 등록
app.include_router(health.router)
app.include_router(users.router)
app.include_router(holdings.router)
app.include_router(recommendations.router)
app.include_router(jobs.router)
