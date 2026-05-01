"""표준 예외 + 핸들러.

API_CONTRACT_SKILL.md ErrorResponse 구조 준수.
새 error_code는 표준 목록에 먼저 등록 후 사용.
"""
from typing import Any, Optional

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from schemas.common import ErrorResponse  # type: ignore[import-not-found]

logger = structlog.get_logger()


class VibeException(Exception):
    """모든 커스텀 예외의 부모."""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = 400,
        job_id: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.job_id = job_id
        self.detail = detail or {}


def _error_response(
    request: Request,
    *,
    error_code: str,
    message: str,
    status_code: int,
    job_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    body = ErrorResponse(
        error_code=error_code,
        message=message,
        job_id=job_id,
        request_id=request_id,
        detail=detail or {},
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


async def vibe_exception_handler(request: Request, exc: VibeException) -> JSONResponse:
    logger.error(
        "request_error",
        error_code=exc.error_code,
        message=exc.message,
        status_code=exc.status_code,
        path=request.url.path,
    )
    return _error_response(
        request,
        error_code=exc.error_code,
        message=exc.message,
        status_code=exc.status_code,
        job_id=exc.job_id,
        detail=exc.detail,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.error(
        "request_error",
        error_code="INVALID_REQUEST",
        path=request.url.path,
        errors=exc.errors(),
    )
    return _error_response(
        request,
        error_code="INVALID_REQUEST",
        message="요청 형식이 올바르지 않습니다",
        status_code=422,
        detail={"errors": exc.errors()},
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """UNIQUE 제약 위반 등 — 일반화된 409로 응답."""
    logger.error("request_error", error_code="INVALID_REQUEST", path=request.url.path)
    return _error_response(
        request,
        error_code="INVALID_REQUEST",
        message="제약 조건 위반 (중복 또는 참조 오류)",
        status_code=409,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """기본 HTTPException을 ErrorResponse 구조로 통일."""
    code_by_status = {
        404: "NOT_FOUND",
        400: "INVALID_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        405: "METHOD_NOT_ALLOWED",
    }
    error_code = code_by_status.get(exc.status_code, "INTERNAL_ERROR")
    return _error_response(
        request,
        error_code=error_code,
        message=str(exc.detail) if exc.detail else error_code,
        status_code=exc.status_code,
    )
