"""Add Phase 2 tables for historical series, macro indicators, news signals, and forecasts.

Revision ID: 0002_phase2_analytics
Revises: 0001_initial_schema
Create Date: 2026-11-20 11:32:00.000000+07:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase2_analytics"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_price_series",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("buy_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("sell_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("retail_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("reference_price_vnd", sa.BigInteger(), nullable=False),
        sa.Column("series_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "is_adjustment_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("source_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "buy_price_vnd IS NULL OR buy_price_vnd > 0",
            name="ck_daily_price_series_buy_price_positive",
        ),
        sa.CheckConstraint(
            "sell_price_vnd IS NULL OR sell_price_vnd > 0",
            name="ck_daily_price_series_sell_price_positive",
        ),
        sa.CheckConstraint(
            "retail_price_vnd IS NULL OR retail_price_vnd > 0",
            name="ck_daily_price_series_retail_price_positive",
        ),
        sa.CheckConstraint(
            "reference_price_vnd > 0",
            name="ck_daily_price_series_reference_price_positive",
        ),
        sa.CheckConstraint(
            "("
            "(buy_price_vnd IS NOT NULL AND sell_price_vnd IS NOT NULL AND retail_price_vnd IS NULL)"
            " OR "
            "(buy_price_vnd IS NULL AND sell_price_vnd IS NULL AND retail_price_vnd IS NOT NULL)"
            ")",
            name="ck_daily_price_series_pricing_shape",
        ),
        sa.CheckConstraint(
            "series_origin IN ('BACKFILL', 'LIVE_ROLLUP')",
            name="ck_daily_price_series_series_origin",
        ),
        sa.ForeignKeyConstraint(
            ["commodity_id"],
            ["commodities.id"],
            name="fk_daily_price_series_commodity_id_commodities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_daily_price_series_location_id_locations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["price_records.id"],
            name="fk_daily_price_series_source_record_id_price_records",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_daily_price_series"),
        sa.UniqueConstraint(
            "commodity_id",
            "location_id",
            "price_date",
            name="uq_daily_price_series_key",
        ),
    )
    op.execute(
        "CREATE INDEX idx_daily_price_series_lookup ON daily_price_series "
        "(commodity_id, location_id, price_date DESC)"
    )
    op.execute(
        "CREATE INDEX idx_daily_price_series_adj ON daily_price_series "
        "(commodity_id, location_id, price_date DESC) WHERE is_adjustment_day = true"
    )

    op.create_table(
        "macro_indicators",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("indicator_code", sa.String(length=32), nullable=False),
        sa.Column("indicator_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("open_value", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("high_value", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("low_value", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("change_pct_1d", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "indicator_code IN ('XAU_USD', 'BRENT_USD', 'WTI_USD', 'DXY', 'USD_VND_VCB')",
            name="ck_macro_indicators_indicator_code",
        ),
        sa.CheckConstraint("value > 0", name="ck_macro_indicators_value_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_macro_indicators"),
        sa.UniqueConstraint(
            "indicator_code",
            "indicator_date",
            name="uq_macro_indicators_code_date",
        ),
    )
    op.execute(
        "CREATE INDEX idx_macro_indicators_code_date_desc ON macro_indicators "
        "(indicator_code, indicator_date DESC)"
    )

    op.create_table(
        "market_news_signals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("publisher", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary_snippet", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("title_dedup_key", sa.CHAR(length=64), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("polarity_score", sa.Numeric(precision=5, scale=3), nullable=False),
        sa.Column("impact_level", sa.String(length=12), nullable=False),
        sa.Column(
            "catalysts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "matched_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("llm_summary_vi", sa.Text(), nullable=True),
        sa.Column("scoring_engine", sa.String(length=24), nullable=False),
        sa.CheckConstraint(
            "domain IN ('GOLD', 'FUEL', 'GAS', 'MACRO')",
            name="ck_market_news_signals_domain",
        ),
        sa.CheckConstraint(
            "polarity_score >= -1.0 AND polarity_score <= 1.0",
            name="ck_market_news_signals_polarity_range",
        ),
        sa.CheckConstraint(
            "impact_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_market_news_signals_impact_level",
        ),
        sa.CheckConstraint(
            "scoring_engine IN ('LEXICON_V1', 'LEXICON_PLUS_GEMINI')",
            name="ck_market_news_signals_scoring_engine",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_news_signals"),
        sa.UniqueConstraint("url_sha256", name="uq_market_news_signals_url_sha256"),
        sa.UniqueConstraint("title_dedup_key", name="uq_market_news_signals_title_dedup_key"),
    )
    op.execute(
        "CREATE INDEX idx_market_news_domain_pub_desc ON market_news_signals "
        "(domain, published_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_market_news_impact_pub_desc ON market_news_signals "
        "(impact_level, published_at DESC)"
    )

    op.create_table(
        "price_forecasts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("horizon_type", sa.String(length=24), nullable=False),
        sa.Column("model_version", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("baseline_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("point_estimate_vnd", sa.BigInteger(), nullable=True),
        sa.Column("lower_bound_vnd", sa.BigInteger(), nullable=True),
        sa.Column("upper_bound_vnd", sa.BigInteger(), nullable=True),
        sa.Column("expected_change_vnd", sa.BigInteger(), nullable=True),
        sa.Column("expected_change_pct", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("predicted_direction", sa.String(length=12), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=5, scale=3), nullable=True),
        sa.Column(
            "news_adjustment_pct",
            sa.Numeric(precision=6, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("insufficient_reason", sa.Text(), nullable=True),
        sa.Column(
            "drivers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "horizon_type IN ('T_PLUS_1', 'T_PLUS_7', 'NEXT_FUEL_CYCLE', 'NEXT_GAS_MONTH')",
            name="ck_price_forecasts_horizon_type",
        ),
        sa.CheckConstraint(
            "status IN ('READY', 'INSUFFICIENT_DATA')",
            name="ck_price_forecasts_status",
        ),
        sa.CheckConstraint(
            "predicted_direction IN ('UP', 'DOWN', 'FLAT', 'UNKNOWN')",
            name="ck_price_forecasts_predicted_direction",
        ),
        sa.CheckConstraint(
            "target_date > forecast_date OR (horizon_type = 'NEXT_FUEL_CYCLE' AND target_date >= forecast_date)",
            name="ck_price_forecasts_target_after_forecast",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_price_forecasts_confidence_range",
        ),
        sa.CheckConstraint(
            "("
            "(status = 'INSUFFICIENT_DATA' AND point_estimate_vnd IS NULL)"
            " OR "
            "(status = 'READY' AND point_estimate_vnd IS NOT NULL AND point_estimate_vnd > 0 "
            "AND lower_bound_vnd IS NOT NULL AND upper_bound_vnd IS NOT NULL "
            "AND lower_bound_vnd <= point_estimate_vnd AND point_estimate_vnd <= upper_bound_vnd)"
            ")",
            name="ck_price_forecasts_status_payload_shape",
        ),
        sa.ForeignKeyConstraint(
            ["commodity_id"],
            ["commodities.id"],
            name="fk_price_forecasts_commodity_id_commodities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_price_forecasts_location_id_locations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_price_forecasts"),
        sa.UniqueConstraint(
            "commodity_id",
            "location_id",
            "forecast_date",
            "horizon_type",
            "model_version",
            name="uq_price_forecasts_snapshot",
        ),
    )
    op.execute(
        "CREATE INDEX idx_price_forecasts_latest ON price_forecasts "
        "(commodity_id, location_id, horizon_type, forecast_date DESC)"
    )
    op.execute(
        "CREATE INDEX idx_price_forecasts_pending_eval ON price_forecasts "
        "(target_date ASC, status) WHERE status = 'READY'"
    )

    op.create_table(
        "forecast_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("forecast_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("horizon_type", sa.String(length=24), nullable=False),
        sa.Column("model_version", sa.String(length=48), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("actual_date", sa.Date(), nullable=False),
        sa.Column("baseline_price_vnd", sa.BigInteger(), nullable=False),
        sa.Column("predicted_price_vnd", sa.BigInteger(), nullable=False),
        sa.Column("actual_price_vnd", sa.BigInteger(), nullable=False),
        sa.Column("abs_error_vnd", sa.BigInteger(), nullable=False),
        sa.Column("pct_error", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("predicted_direction", sa.String(length=12), nullable=False),
        sa.Column("actual_direction", sa.String(length=12), nullable=False),
        sa.Column("direction_correct", sa.Boolean(), nullable=False),
        sa.Column("within_interval", sa.Boolean(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actual_price_vnd > 0",
            name="ck_forecast_evaluations_actual_price_positive",
        ),
        sa.CheckConstraint(
            "predicted_price_vnd > 0",
            name="ck_forecast_evaluations_predicted_price_positive",
        ),
        sa.CheckConstraint(
            "abs_error_vnd >= 0",
            name="ck_forecast_evaluations_abs_error_non_negative",
        ),
        sa.CheckConstraint(
            "pct_error >= 0",
            name="ck_forecast_evaluations_pct_error_non_negative",
        ),
        sa.CheckConstraint(
            "predicted_direction IN ('UP', 'DOWN', 'FLAT')",
            name="ck_forecast_evaluations_predicted_direction",
        ),
        sa.CheckConstraint(
            "actual_direction IN ('UP', 'DOWN', 'FLAT')",
            name="ck_forecast_evaluations_actual_direction",
        ),
        sa.ForeignKeyConstraint(
            ["commodity_id"],
            ["commodities.id"],
            name="fk_forecast_evaluations_commodity_id_commodities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["forecast_id"],
            ["price_forecasts.id"],
            name="fk_forecast_evaluations_forecast_id_price_forecasts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_forecast_evaluations_location_id_locations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_evaluations"),
        sa.UniqueConstraint("forecast_id", name="uq_forecast_evaluations_forecast_id"),
    )
    op.execute(
        "CREATE INDEX idx_forecast_evaluations_target_desc ON forecast_evaluations "
        "(target_date DESC, evaluated_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_forecast_evaluations_group ON forecast_evaluations "
        "(commodity_id, horizon_type, target_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_forecast_evaluations_group")
    op.execute("DROP INDEX IF EXISTS idx_forecast_evaluations_target_desc")
    op.drop_table("forecast_evaluations")

    op.execute("DROP INDEX IF EXISTS idx_price_forecasts_pending_eval")
    op.execute("DROP INDEX IF EXISTS idx_price_forecasts_latest")
    op.drop_table("price_forecasts")

    op.execute("DROP INDEX IF EXISTS idx_market_news_impact_pub_desc")
    op.execute("DROP INDEX IF EXISTS idx_market_news_domain_pub_desc")
    op.drop_table("market_news_signals")

    op.execute("DROP INDEX IF EXISTS idx_macro_indicators_code_date_desc")
    op.drop_table("macro_indicators")

    op.execute("DROP INDEX IF EXISTS idx_daily_price_series_adj")
    op.execute("DROP INDEX IF EXISTS idx_daily_price_series_lookup")
    op.drop_table("daily_price_series")

