"""Initial vxg-tracker schema with 8 normalized tables and partial indexes.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-19 08:05:00.000000+07:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("location_type", sa.String(length=16), nullable=False),
        sa.Column("macro_region", sa.String(length=16), nullable=False),
        sa.Column("fuel_zone_code", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("location_type IN ('CITY', 'FUEL_ZONE')", name="ck_locations_location_type"),
        sa.CheckConstraint(
            "macro_region IN ('NORTH', 'CENTRAL', 'SOUTH', 'NATIONAL')",
            name="ck_locations_macro_region",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_locations"),
        sa.UniqueConstraint("code", name="uq_locations_code"),
    )

    op.create_table(
        "commodities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=150), nullable=False),
        sa.Column("spec_detail", sa.String(length=150), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("pricing_type", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("category IN ('GOLD', 'FUEL', 'GAS')", name="ck_commodities_category"),
        sa.CheckConstraint(
            "unit IN ('VND_PER_LUONG', 'VND_PER_LITER', 'VND_PER_CYLINDER')",
            name="ck_commodities_unit",
        ),
        sa.CheckConstraint(
            "pricing_type IN ('BUY_SELL', 'SINGLE_RETAIL')",
            name="ck_commodities_pricing_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commodities"),
        sa.UniqueConstraint("code", name="uq_commodities_code"),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.String(length=24), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("fetch_strategy", sa.String(length=24), nullable=False),
        sa.Column("parser_key", sa.String(length=64), nullable=False),
        sa.Column("schedule_cron", sa.String(length=64), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), server_default=sa.text("15000"), nullable=False),
        sa.Column(
            "rate_limit_delay_ms", sa.Integer(), server_default=sa.text("2000"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("category IN ('GOLD', 'FUEL', 'GAS')", name="ck_sources_category"),
        sa.CheckConstraint(
            "tier IN ('PRIMARY_OFFICIAL', 'SECONDARY_CROSSCHECK')",
            name="ck_sources_tier",
        ),
        sa.CheckConstraint(
            "fetch_strategy IN ('HTTP_XML', 'HTTP_JSON', 'HTTP_HTML', 'PLAYWRIGHT_JS')",
            name="ck_sources_fetch_strategy",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )

    op.create_table(
        "crawl_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_code", sa.String(length=48), nullable=False),
        sa.Column("scope_category", sa.String(length=16), nullable=False),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "sources_queried_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "pages_fetched_success", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "pages_fetched_failed", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "documents_parsed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "records_extracted_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "records_verified_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "records_flagged_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "records_unchanged_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "scope_category IN ('ALL', 'GOLD', 'FUEL', 'GAS')",
            name="ck_crawl_runs_scope_category",
        ),
        sa.CheckConstraint(
            "trigger_type IN ('SCHEDULED', 'MANUAL_ADMIN', 'RETRY')",
            name="ck_crawl_runs_trigger_type",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'PARTIAL_SUCCESS', 'FAILED')",
            name="ck_crawl_runs_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_crawl_runs"),
        sa.UniqueConstraint("run_code", name="uq_crawl_runs_run_code"),
    )
    op.execute("CREATE INDEX idx_crawl_runs_created_desc ON crawl_runs (started_at DESC, status)")

    op.create_table(
        "raw_evidence_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("compressed_payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_raw_evidence_snapshots"),
        sa.UniqueConstraint("content_sha256", name="uq_raw_evidence_snapshots_content_sha256"),
    )

    op.create_table(
        "crawl_source_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("fetch_strategy_used", sa.String(length=24), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("attempt_number", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("fetch_status", sa.String(length=24), nullable=False),
        sa.Column("documents_parsed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_extracted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_verified", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_flagged", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "parser_metadata",
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
            "fetch_status IN ('SUCCESS', 'HTTP_ERROR', 'TIMEOUT', 'PARSE_ERROR', 'SCHEMA_DRIFT', 'BLOCKED')",
            name="ck_crawl_source_logs_fetch_status",
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_crawl_source_logs_crawl_run_id_crawl_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["raw_evidence_snapshots.id"],
            name="fk_crawl_source_logs_snapshot_id_raw_evidence_snapshots",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_crawl_source_logs_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_crawl_source_logs"),
    )
    op.execute(
        "CREATE INDEX idx_crawl_source_logs_run_id ON crawl_source_logs (crawl_run_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_crawl_source_logs_source_health ON crawl_source_logs (source_id, created_at DESC)"
    )

    op.create_table(
        "price_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("cross_check_source_id", sa.Integer(), nullable=True),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_log_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buy_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("sell_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("retail_price_vnd", sa.BigInteger(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "crawled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("cross_check_delta_pct", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column(
            "validation_notes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.CHAR(length=64), nullable=False),
        sa.CheckConstraint(
            "buy_price_vnd IS NULL OR buy_price_vnd > 0",
            name="ck_price_records_buy_price_positive",
        ),
        sa.CheckConstraint(
            "sell_price_vnd IS NULL OR sell_price_vnd > 0",
            name="ck_price_records_sell_price_positive",
        ),
        sa.CheckConstraint(
            "retail_price_vnd IS NULL OR retail_price_vnd > 0",
            name="ck_price_records_retail_price_positive",
        ),
        sa.CheckConstraint(
            "("
            "(buy_price_vnd IS NOT NULL AND sell_price_vnd IS NOT NULL AND retail_price_vnd IS NULL)"
            " OR "
            "(buy_price_vnd IS NULL AND sell_price_vnd IS NULL AND retail_price_vnd IS NOT NULL)"
            ")",
            name="ck_price_records_pricing_shape",
        ),
        sa.CheckConstraint(
            "verification_status IN ('VERIFIED', 'CROSS_CHECKED', 'FLAGGED', 'REJECTED', 'OVERRIDDEN_MANUAL')",
            name="ck_price_records_verification_status",
        ),
        sa.ForeignKeyConstraint(
            ["commodity_id"],
            ["commodities.id"],
            name="fk_price_records_commodity_id_commodities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_price_records_crawl_run_id_crawl_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cross_check_source_id"],
            ["sources.id"],
            name="fk_price_records_cross_check_source_id_sources",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_price_records_location_id_locations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_price_records_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_log_id"],
            ["crawl_source_logs.id"],
            name="fk_price_records_source_log_id_crawl_source_logs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_price_records"),
        sa.UniqueConstraint("idempotency_key", name="uq_price_records_idempotency_key"),
    )
    op.execute(
        "CREATE INDEX idx_price_records_latest_verified ON price_records "
        "(commodity_id, location_id, effective_date DESC, COALESCE(source_published_at, crawled_at) DESC, crawled_at DESC) "
        "WHERE verification_status IN ('VERIFIED', 'CROSS_CHECKED', 'OVERRIDDEN_MANUAL')"
    )
    op.execute(
        "CREATE INDEX idx_price_records_timeseries ON price_records "
        "(commodity_id, location_id, effective_date DESC, crawled_at DESC)"
    )
    op.execute("CREATE INDEX idx_price_records_crawl_run ON price_records (crawl_run_id)")

    op.create_table(
        "anomaly_alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "alert_type IN ('SCHEMA_DRIFT', 'SOURCE_DOWN_3X', 'PRICE_SPIKE', 'CROSS_CHECK_MISMATCH', 'VALIDATION_ERROR')",
            name="ck_anomaly_alerts_alert_type",
        ),
        sa.CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_anomaly_alerts_severity",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')",
            name="ck_anomaly_alerts_status",
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_anomaly_alerts_crawl_run_id_crawl_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["price_record_id"],
            ["price_records.id"],
            name="fk_anomaly_alerts_price_record_id_price_records",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_log_id"],
            ["crawl_source_logs.id"],
            name="fk_anomaly_alerts_source_log_id_crawl_source_logs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_anomaly_alerts"),
    )
    op.execute(
        "CREATE INDEX idx_anomaly_alerts_open ON anomaly_alerts (status, created_at DESC) "
        "WHERE status = 'OPEN'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_anomaly_alerts_open")
    op.drop_table("anomaly_alerts")

    op.execute("DROP INDEX IF EXISTS idx_price_records_crawl_run")
    op.execute("DROP INDEX IF EXISTS idx_price_records_timeseries")
    op.execute("DROP INDEX IF EXISTS idx_price_records_latest_verified")
    op.drop_table("price_records")

    op.execute("DROP INDEX IF EXISTS idx_crawl_source_logs_source_health")
    op.execute("DROP INDEX IF EXISTS idx_crawl_source_logs_run_id")
    op.drop_table("crawl_source_logs")

    op.drop_table("raw_evidence_snapshots")

    op.execute("DROP INDEX IF EXISTS idx_crawl_runs_created_desc")
    op.drop_table("crawl_runs")

    op.drop_table("sources")
    op.drop_table("commodities")
    op.drop_table("locations")

