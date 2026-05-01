"""
공통 스키마 — 모든 모듈에서 재사용.

API_CONTRACT_SKILL.md의 표준 에러/Job 응답 구조 정의.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobStatusEnum(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorResponse(BaseModel):
    """모든 에러 응답의 표준 구조."""

    error_code: str
    message: str
    job_id: Optional[str] = None
    request_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    detail: dict[str, Any] = Field(default_factory=dict)


class JobQueuedResponse(BaseModel):
    """Job 제출 즉시 응답."""

    job_id: str
    status: JobStatusEnum = JobStatusEnum.QUEUED
    message: str = "작업이 대기열에 추가되었습니다"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JobStatusResponse(BaseModel):
    """Job 상태 조회 응답."""

    job_id: str
    status: JobStatusEnum
    progress: int = Field(ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True
