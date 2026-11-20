"""Separate LIVE vs BACKTEST forecast evaluations and add forecast_runs job queue table.

Revision ID: 0004_forecast_eval_type
Revises: 0003_macro_usd_vnd_market
Create Date: 2026-11-20 12:30:00.000000+07:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_forecast_eval_type"
down_revision: str | Sequence[str] | None = "0003_macro_usd_vnd_market"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "forecast_evaluations",
        sa.Column(
            "evaluation_type",
            sa.String(length=16),
            server_default=sa.text("'LIVE'"),
            nullable=False,
        ),
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column("lower_bound_vnd", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column("upper_bound_vnd", sa.BigInteger(), nullable=True),
    )
    op.alter_column(
        "forecast_evaluations",
        "forecast_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_forecast_evaluations_evaluation_type",
        "forecast_evaluations",
        "evaluation_type IN ('LIVE', 'BACKTEST')",
    )
    op.create_check_constraint(
        "ck_forecast_evaluations_eval_type_forecast_link",
        "forecast_evaluations",
        "(evaluation_type = 'LIVE' AND forecast_id IS NOT NULL) "
        "OR (evaluation_type = 'BACKTEST' AND forecast_id IS NULL)",
    )
    op.execute(
        "CREATE INDEX idx_forecast_evaluations_type_target ON forecast_evaluations "
        "(evaluation_type, target_date DESC, evaluated_at DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_forecast_eval_backtest_key ON forecast_evaluations "
        "(commodity_id, location_id, forecast_date, target_date, horizon_type, model_version) "
        "WHERE evaluation_type = 'BACKTEST'"
    )

    op.create_table(
        "forecast_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_code", sa.String(length=64), nullable=False),
        sa.Column("scope_category", sa.String(length=16), nullable=False),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "forecasts_generated",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "evaluations_created",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope_category IN ('ALL', 'GOLD', 'FUEL', 'GAS')",
            name="ck_forecast_runs_scope_category",
        ),
        sa.CheckConstraint(
            "trigger_type IN ('SCHEDULED', 'MANUAL_ADMIN', 'RETRY')",
            name="ck_forecast_runs_trigger_type",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'PARTIAL_SUCCESS', 'FAILED')",
            name="ck_forecast_runs_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_runs"),
        sa.UniqueConstraint("run_code", name="uq_forecast_runs_run_code"),
    )
    op.execute(
        "CREATE INDEX idx_forecast_runs_created_desc ON forecast_runs (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_forecast_runs_created_desc")
    op.drop_table("forecast_runs")

    op.execute("DROP INDEX IF EXISTS uq_forecast_eval_backtest_key")
    op.execute("DROP INDEX IF EXISTS idx_forecast_evaluations_type_target")
    op.execute("DELETE FROM forecast_evaluations WHERE evaluation_type = 'BACKTEST'")
    op.drop_constraint(
        "ck_forecast_evaluations_eval_type_forecast_link",
        "forecast_evaluations",
        type_="check",
    )
    op.drop_constraint(
        "ck_forecast_evaluations_evaluation_type",
        "forecast_evaluations",
        type_="check",
    )
    op.alter_column(
        "forecast_evaluations",
        "forecast_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("forecast_evaluations", "upper_bound_vnd")
    op.drop_column("forecast_evaluations", "lower_bound_vnd")
    op.drop_column("forecast_evaluations", "evaluation_type")

