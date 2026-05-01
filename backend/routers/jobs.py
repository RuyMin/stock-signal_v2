"""jobs 상태 조회 — READ only."""
import uuid

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.exceptions import VibeException
from models.job import Job
from schemas.common import JobStatusResponse  # type: ignore[import-not-found]

logger = structlog.get_logger()

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobStatusResponse:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise VibeException(
            error_code="INVALID_REQUEST",
            message="job_id 형식이 올바르지 않습니다 (UUID 필요)",
            status_code=400,
            detail={"job_id": job_id},
        )

    job = await db.get(Job, job_uuid)
    if job is None:
        raise VibeException(
            error_code="JOB_NOT_FOUND",
            message="해당 job을 찾을 수 없습니다",
            status_code=404,
            job_id=job_id,
        )

    logger.info("job_status_queried", job_id=str(job.id), status=job.status)

    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=job.result,
        error_message=job.error_msg,
    )
