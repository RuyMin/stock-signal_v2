"""Add weekly_macro_reports table.

Revision ID: 20260514_0002
Revises: 20260514_0001
Create Date: 2026-05-14

etf-and-weekly-macro spec Phase 4-6: 주간 매크로 리포트 audit + idempotency 테이블.
UNIQUE(week_start)로 같은 주에 두 번 트리거되어도 UPSERT.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260514_0002"
down_revision = "20260514_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_macro_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("job_id", sa.dialects.postgresql.UUID(), nullable=True),
        sa.Column("macro_summary", sa.Text(), nullable=True),
        sa.Column("macro_values", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("etf_evaluations", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("week_start", name="weekly_macro_reports_week_start_unique"),
    )


def downgrade() -> None:
    op.drop_table("weekly_macro_reports")
