"""Add momentum indicators to signals table.

Revision ID: 20260108_0001
Revises: 20260503_0001
Create Date: 2026-01-08

모멘텀 기반 신호 수집 및 평가 기능 추가를 위한 스키마 확장.
signals 테이블에 7개 컬럼 추가:
- one_day_net_buy: 1일 순매수 (기관+외국인 합산)
- three_day_avg_net_buy: 3일 평균 순매수
- volume_ratio: 거래량 비율 (당일/20일 평균)
- rsi: 14기간 RSI (0-100)
- ma_alignment: 이동평균선 배열 상태 (bullish/bearish/neutral)
- bollinger_position: 볼린저밴드 내 위치 (0-1)
- trading_value: 거래대금 (원화)

모든 컬럼은 nullable=True, default=NULL로 하위 호환성 유지.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260108_0001"
down_revision = "20260503_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add momentum indicators
    op.add_column(
        "signals",
        sa.Column("one_day_net_buy", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("three_day_avg_net_buy", sa.BigInteger(), nullable=True),
    )
    
    # Add volume indicators
    op.add_column(
        "signals",
        sa.Column("volume_ratio", sa.Numeric(6, 2), nullable=True),
    )
    
    # Add technical indicators
    op.add_column(
        "signals",
        sa.Column("rsi", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("ma_alignment", sa.String(20), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("bollinger_position", sa.Numeric(6, 3), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("trading_value", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    """주의: 모멘텀 지표 데이터가 손실됨. 운영에서 함부로 호출 금지."""
    op.drop_column("signals", "trading_value")
    op.drop_column("signals", "bollinger_position")
    op.drop_column("signals", "ma_alignment")
    op.drop_column("signals", "rsi")
    op.drop_column("signals", "volume_ratio")
    op.drop_column("signals", "three_day_avg_net_buy")
    op.drop_column("signals", "one_day_net_buy")
