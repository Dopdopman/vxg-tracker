"""Allow USD_VND_MARKET in macro_indicators for 2-year Yahoo FX history alongside VCB.

Revision ID: 0003_macro_usd_vnd_market
Revises: 0002_phase2_analytics
Create Date: 2026-11-20 12:10:00.000000+07:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_macro_usd_vnd_market"
down_revision: str | Sequence[str] | None = "0002_phase2_analytics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_macro_indicators_indicator_code",
        "macro_indicators",
        type_="check",
    )
    op.create_check_constraint(
        "ck_macro_indicators_indicator_code",
        "macro_indicators",
        "indicator_code IN ('XAU_USD', 'BRENT_USD', 'WTI_USD', 'DXY', 'USD_VND_VCB', 'USD_VND_MARKET')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM macro_indicators WHERE indicator_code = 'USD_VND_MARKET'")
    op.drop_constraint(
        "ck_macro_indicators_indicator_code",
        "macro_indicators",
        type_="check",
    )
    op.create_check_constraint(
        "ck_macro_indicators_indicator_code",
        "macro_indicators",
        "indicator_code IN ('XAU_USD', 'BRENT_USD', 'WTI_USD', 'DXY', 'USD_VND_VCB')",
    )

