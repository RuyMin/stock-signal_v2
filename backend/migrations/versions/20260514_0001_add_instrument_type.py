"""Add holdings.instrument_type column + backfill from name patterns.

Revision ID: 20260514_0001
Revises: 20260108_0001
Create Date: 2026-05-14

ETF/단일주 평가 분리(etf-and-weekly-macro spec)를 위한 holdings 테이블 확장.

컬럼: instrument_type VARCHAR(20) NOT NULL DEFAULT 'single_stock'
허용값: 'single_stock' | 'index_etf' | 'sector_etf'

부분 인덱스: instrument_type != 'single_stock'만 (대부분이 single_stock인 워크로드)

백필: 기존 row의 name을 infer_instrument_type으로 분석하여 UPDATE.
"""
import sqlalchemy as sa
from alembic import op

from schemas.holdings import infer_instrument_type  # type: ignore[import-not-found]

revision = "20260514_0001"
down_revision = "20260108_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "holdings",
        sa.Column(
            "instrument_type",
            sa.String(20),
            nullable=False,
            server_default="single_stock",
        ),
    )
    op.create_index(
        "idx_holdings_instrument_type",
        "holdings",
        ["instrument_type"],
        postgresql_where=sa.text("instrument_type != 'single_stock'"),
    )

    # 백필: 기존 row를 name 기반 자동 분류로 UPDATE.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name FROM holdings")).fetchall()
    for row in rows:
        inferred = infer_instrument_type(row.name)
        if inferred != "single_stock":
            conn.execute(
                sa.text("UPDATE holdings SET instrument_type = :t WHERE id = :id"),
                {"t": inferred, "id": row.id},
            )


def downgrade() -> None:
    op.drop_index("idx_holdings_instrument_type", table_name="holdings")
    op.drop_column("holdings", "instrument_type")
