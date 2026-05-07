"""users CRUD — 텔레그램 봇 multi-user 화이트리스트.

API:
  POST   /users/register             — listener가 /start 시 호출, status=pending
  POST   /users/{chat_id}/approve    — admin이 신규 사용자 승인 (status=active)
  GET    /users                      — admin이 전체 사용자 목록 조회
  GET    /users/by-chat-id/{chat_id} — listener가 chat_id로 user 조회 (인증 체크용)

소규모(10명 이내) 정책:
- 첫 admin은 .env의 TELEGRAM_ADMIN_CHAT_IDS 화이트리스트 또는 마이그레이션 시드로 부트스트랩
- /start 시 자동으로 users.pending row 생성, admin 승인 전까지는 명령어 차단
- backend는 인증 체크만 수행. 실제 chat_id 기반 권한 결정은 listener가 함
"""
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.exceptions import VibeException
from models.user import User
from schemas.users import (  # type: ignore[import-not-found]
    UserApproveRequest,
    UserListResponse,
    UserRegisterRequest,
    UserResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserRegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """텔레그램 /start 진입점. status=pending으로 INSERT (이미 있으면 그대로 반환).

    응답 코드: 신규 생성 시 201, 이미 존재 시 200 (listener가 admin 알림 트리거 판단에 사용).
    """
    existing = await db.execute(select(User).where(User.chat_id == payload.chat_id))
    user = existing.scalar_one_or_none()
    if user is not None:
        response.status_code = status.HTTP_200_OK
        return UserResponse.model_validate(user)

    user = User(
        chat_id=payload.chat_id,
        telegram_username=payload.telegram_username,
        status="pending",
        is_admin=False,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise VibeException(
            error_code="INVALID_REQUEST",
            message="동시 등록 충돌",
            status_code=409,
            detail={"chat_id": payload.chat_id},
        )
    await db.refresh(user)
    logger.info("user_registered", chat_id=user.chat_id, user_id=user.id)
    return UserResponse.model_validate(user)


@router.post("/{chat_id}/approve", response_model=UserResponse)
async def approve_user(
    chat_id: int,
    payload: UserApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """admin이 pending 사용자 승인 → status=active.

    승인자가 admin인지는 backend가 검증 (approved_by_chat_id가 is_admin=true인지).
    """
    admin_q = await db.execute(
        select(User).where(User.chat_id == payload.approved_by_chat_id)
    )
    admin = admin_q.scalar_one_or_none()
    if admin is None or not admin.is_admin or admin.status != "active":
        raise VibeException(
            error_code="FORBIDDEN",
            message="admin 권한이 없습니다",
            status_code=403,
            detail={"approved_by_chat_id": payload.approved_by_chat_id},
        )

    target_q = await db.execute(select(User).where(User.chat_id == chat_id))
    target = target_q.scalar_one_or_none()
    if target is None:
        raise VibeException(
            error_code="USER_NOT_FOUND",
            message="대상 사용자가 등록되지 않았습니다",
            status_code=404,
            detail={"chat_id": chat_id},
        )

    target.status = "active"
    target.approved_by = admin.id
    target.approved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(target)
    logger.info("user_approved", chat_id=chat_id, approved_by_chat_id=admin.chat_id)
    return UserResponse.model_validate(target)


@router.get("/by-chat-id/{chat_id}", response_model=UserResponse)
async def get_user_by_chat_id(
    chat_id: int, db: AsyncSession = Depends(get_db)
) -> UserResponse:
    """listener가 명령어 처리 전 사용자 인증/상태 확인."""
    q = await db.execute(select(User).where(User.chat_id == chat_id))
    user = q.scalar_one_or_none()
    if user is None:
        raise VibeException(
            error_code="USER_NOT_FOUND",
            message="등록되지 않은 사용자",
            status_code=404,
            detail={"chat_id": chat_id},
        )
    return UserResponse.model_validate(user)


@router.get("", response_model=UserListResponse)
async def list_users(db: AsyncSession = Depends(get_db)) -> UserListResponse:
    """admin이 사용자 목록 확인 (현재는 권한 체크 없음 — 단일 호출자 listener)."""
    result = await db.execute(select(User).order_by(User.registered_at.desc()))
    items = [UserResponse.model_validate(u) for u in result.scalars().all()]
    return UserListResponse(items=items, total=len(items))
