"""Add users table and holdings.user_id FK (multi-user support).

Revision ID: 20260430_0001
Revises: (baseline = init.sql)
Create Date: 2026-04-30

기존 단일 사용자(.env의 TELEGRAM_AUTHORIZED_CHAT_ID 단일값) → 소규모 multi-user(10명 이내)
화이트리스트로 전환. 추천(recommendations / signals / news / macro_indicators / jobs)은
시장 공통이라 user 분리 없음. holdings만 사용자별로 분리.

이 마이그레이션은 멱등성을 가지지 않음 — 한 번만 적용해야 함.
운영 적용 시 환경변수 STOCK_SIGNAL_BOOTSTRAP_ADMIN_CHAT_ID로 admin 첫 사용자 시드.
값 미설정 시 holdings 데이터를 옮길 곳이 없어 마이그레이션 실패 — 명시적으로 막음.
"""
import os

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260430_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. users 테이블 신설
    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("telegram_username", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("approved_by", sa.dialects.postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("registered_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_users_chat_id", "users", ["chat_id"])
    op.create_index(
        "idx_users_status", "users", ["status"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # 2. holdings.user_id 추가 (먼저 nullable, 백필 후 NOT NULL)
    op.add_column(
        "holdings",
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=False)),
    )

    # 3. 기존 holdings 데이터 → admin user에 백필
    conn = op.get_bind()
    existing = conn.execute(sa.text("SELECT COUNT(*) FROM holdings")).scalar() or 0
    admin_chat_raw = os.environ.get("STOCK_SIGNAL_BOOTSTRAP_ADMIN_CHAT_ID", "").strip()

    if existing > 0:
        if not admin_chat_raw:
            raise RuntimeError(
                f"holdings 테이블에 {existing} rows 있음 — admin user 시드 없이 user_id NOT NULL 적용 불가. "
                "환경변수 STOCK_SIGNAL_BOOTSTRAP_ADMIN_CHAT_ID=<텔레그램 chat_id>로 admin 시드 후 재실행."
            )
        admin_chat_id = int(admin_chat_raw)
        admin_id = conn.execute(
            sa.text(
                "INSERT INTO users (chat_id, status, is_admin) "
                "VALUES (:c, 'active', TRUE) RETURNING id"
            ),
            {"c": admin_chat_id},
        ).scalar()
        conn.execute(
            sa.text("UPDATE holdings SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": admin_id},
        )
    elif admin_chat_raw:
        # holdings 비어있어도 admin 시드 환경변수 있으면 첫 admin user 생성 (편의)
        conn.execute(
            sa.text(
                "INSERT INTO users (chat_id, status, is_admin) VALUES (:c, 'active', TRUE)"
            ),
            {"c": int(admin_chat_raw)},
        )

    # 4. user_id NOT NULL + FK + UNIQUE 변경
    op.alter_column("holdings", "user_id", nullable=False)
    op.create_foreign_key(
        "holdings_user_id_fkey", "holdings", "users", ["user_id"], ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("holdings_ticker_key", "holdings", type_="unique")
    op.create_unique_constraint("holdings_user_ticker_unique", "holdings", ["user_id", "ticker"])
    op.create_index("idx_holdings_user_id", "holdings", ["user_id"])


def downgrade() -> None:
    """주의: holdings 데이터의 user 분리 정보가 손실됨. 운영에서 함부로 호출 금지."""
    op.drop_index("idx_holdings_user_id", table_name="holdings")
    op.drop_constraint("holdings_user_ticker_unique", "holdings", type_="unique")
    op.drop_constraint("holdings_user_id_fkey", "holdings", type_="foreignkey")
    op.create_unique_constraint("holdings_ticker_key", "holdings", ["ticker"])
    op.drop_column("holdings", "user_id")

    op.drop_index("idx_users_status", table_name="users")
    op.drop_index("idx_users_chat_id", table_name="users")
    op.drop_table("users")
