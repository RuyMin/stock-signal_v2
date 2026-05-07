"""Add holdings.avg_price column.

Revision ID: 20260503_0001
Revises: 20260430_0001
Create Date: 2026-05-03

사용자가 보유 종목의 평단가를 직접 입력할 수 있도록 holdings.avg_price 컬럼 추가.
NUMERIC(12,2), nullable — 기존 row는 NULL로 시작, /edit 명령으로 채울 수 있음.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260503_0001"
down_revision = "20260430_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "holdings",
        sa.Column("avg_price", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("holdings", "avg_price")
