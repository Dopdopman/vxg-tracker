"""SQLAlchemy 2.0 ORM definitions for all 13 vxg-tracker tables (8 Phase 1 + 5 Phase 2)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vxg_shared.db.base import Base


class Location(Base):
    """Cities (HN/DN/HCM) and Fuel pricing zones (VUNG_1/VUNG_2)."""

    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("location_type IN ('CITY', 'FUEL_ZONE')", name="location_type"),
        CheckConstraint(
            "macro_region IN ('NORTH', 'CENTRAL', 'SOUTH', 'NATIONAL')",
            name="macro_region",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    location_type: Mapped[str] = mapped_column(String(16), nullable=False)
    macro_region: Mapped[str] = mapped_column(String(16), nullable=False)
    fuel_zone_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Commodity(Base):
    """Tracked essential commodity items across GOLD, FUEL, and GAS."""

    __tablename__ = "commodities"
    __table_args__ = (
        CheckConstraint("category IN ('GOLD', 'FUEL', 'GAS')", name="category"),
        CheckConstraint(
            "unit IN ('VND_PER_LUONG', 'VND_PER_LITER', 'VND_PER_CYLINDER')",
            name="unit",
        ),
        CheckConstraint("pricing_type IN ('BUY_SELL', 'SINGLE_RETAIL')", name="pricing_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    brand: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(150), nullable=False)
    spec_detail: Mapped[str | None] = mapped_column(String(150), nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    pricing_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Source(Base):
    """Official Tier 1 and Cross-Check Tier 2 Vietnamese data sources."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("category IN ('GOLD', 'FUEL', 'GAS')", name="category"),
        CheckConstraint(
            "tier IN ('PRIMARY_OFFICIAL', 'SECONDARY_CROSSCHECK')",
            name="tier",
        ),
        CheckConstraint(
            "fetch_strategy IN ('HTTP_XML', 'HTTP_JSON', 'HTTP_HTML', 'PLAYWRIGHT_JS')",
            name="fetch_strategy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    tier: Mapped[str] = mapped_column(String(24), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetch_strategy: Mapped[str] = mapped_column(String(24), nullable=False)
    parser_key: Mapped[str] = mapped_column(String(64), nullable=False)
    schedule_cron: Mapped[str] = mapped_column(String(64), nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("15000"))
    rate_limit_delay_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2000")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CrawlRun(Base):
    """Job-level audit telemetry capturing full transparency counters per run."""

    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint("scope_category IN ('ALL', 'GOLD', 'FUEL', 'GAS')", name="scope_category"),
        CheckConstraint(
            "trigger_type IN ('SCHEDULED', 'MANUAL_ADMIN', 'RETRY')",
            name="trigger_type",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'PARTIAL_SUCCESS', 'FAILED')",
            name="status",
        ),
        Index("idx_crawl_runs_created_desc", text("started_at DESC"), "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    scope_category: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    sources_queried_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    pages_fetched_success: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    pages_fetched_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    documents_parsed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    records_extracted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    records_verified_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    records_flagged_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    records_unchanged_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_logs: Mapped[list[CrawlSourceLog]] = relationship(
        back_populates="crawl_run", cascade="all, delete-orphan"
    )


class RawEvidenceSnapshot(Base):
    """Deduplicated gzip-compressed source payload archive keyed by SHA-256."""

    __tablename__ = "raw_evidence_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    content_sha256: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    compressed_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CrawlSourceLog(Base):
    """Per-source request and parse telemetry inside each CrawlRun."""

    __tablename__ = "crawl_source_logs"
    __table_args__ = (
        CheckConstraint(
            "fetch_status IN ('SUCCESS', 'HTTP_ERROR', 'TIMEOUT', 'PARSE_ERROR', 'SCHEMA_DRIFT', 'BLOCKED')",
            name="fetch_status",
        ),
        Index("idx_crawl_source_logs_run_id", "crawl_run_id", text("created_at DESC")),
        Index("idx_crawl_source_logs_source_health", "source_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_evidence_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fetch_strategy_used: Mapped[str] = mapped_column(String(24), nullable=False)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    fetch_status: Mapped[str] = mapped_column(String(24), nullable=False)
    documents_parsed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_extracted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    records_verified: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_flagged: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    crawl_run: Mapped[CrawlRun] = relationship(back_populates="source_logs")
    source: Mapped[Source] = relationship()
    snapshot: Mapped[RawEvidenceSnapshot | None] = relationship()


class PriceRecord(Base):
    """Normalized, verified, and quarantined VND integer time-series price records."""

    __tablename__ = "price_records"
    __table_args__ = (
        CheckConstraint("buy_price_vnd IS NULL OR buy_price_vnd > 0", name="buy_price_positive"),
        CheckConstraint("sell_price_vnd IS NULL OR sell_price_vnd > 0", name="sell_price_positive"),
        CheckConstraint(
            "retail_price_vnd IS NULL OR retail_price_vnd > 0",
            name="retail_price_positive",
        ),
        CheckConstraint(
            "("
            "(buy_price_vnd IS NOT NULL AND sell_price_vnd IS NOT NULL AND retail_price_vnd IS NULL)"
            " OR "
            "(buy_price_vnd IS NULL AND sell_price_vnd IS NULL AND retail_price_vnd IS NOT NULL)"
            ")",
            name="pricing_shape",
        ),
        CheckConstraint(
            "verification_status IN ('VERIFIED', 'CROSS_CHECKED', 'FLAGGED', 'REJECTED', 'OVERRIDDEN_MANUAL')",
            name="verification_status",
        ),
        Index(
            "idx_price_records_latest_verified",
            "commodity_id",
            "location_id",
            text("effective_date DESC"),
            text("COALESCE(source_published_at, crawled_at) DESC"),
            text("crawled_at DESC"),
            postgresql_where=text(
                "verification_status IN ('VERIFIED', 'CROSS_CHECKED', 'OVERRIDDEN_MANUAL')"
            ),
        ),
        Index(
            "idx_price_records_timeseries",
            "commodity_id",
            "location_id",
            text("effective_date DESC"),
            text("crawled_at DESC"),
        ),
        Index("idx_price_records_crawl_run", "crawl_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodities.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    cross_check_source_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id", ondelete="RESTRICT"), nullable=False
    )
    source_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_source_logs.id", ondelete="RESTRICT"), nullable=False
    )
    buy_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retail_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False)
    cross_check_delta_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    validation_notes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    idempotency_key: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)

    commodity: Mapped[Commodity] = relationship()
    location: Mapped[Location] = relationship()
    source: Mapped[Source] = relationship(foreign_keys=[source_id])
    cross_check_source: Mapped[Source | None] = relationship(foreign_keys=[cross_check_source_id])
    crawl_run: Mapped[CrawlRun] = relationship()
    source_log: Mapped[CrawlSourceLog] = relationship()


class AnomalyAlert(Base):
    """Operational alerts triggered by schema drift, source outages, or flagged prices."""

    __tablename__ = "anomaly_alerts"
    __table_args__ = (
        CheckConstraint(
            "alert_type IN ('SCHEMA_DRIFT', 'SOURCE_DOWN_3X', 'PRICE_SPIKE', 'CROSS_CHECK_MISMATCH', 'VALIDATION_ERROR')",
            name="alert_type",
        ),
        CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name="severity"),
        CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name="status"),
        Index(
            "idx_anomaly_alerts_open",
            "status",
            text("created_at DESC"),
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )
    source_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_source_logs.id", ondelete="SET NULL"), nullable=True
    )
    price_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_records.id", ondelete="SET NULL"), nullable=True
    )
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'OPEN'"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailyPriceSeries(Base):
    """Normalized daily reference series populated via backfill and Phase 1 live rollups."""

    __tablename__ = "daily_price_series"
    __table_args__ = (
        UniqueConstraint("commodity_id", "location_id", "price_date", name="uq_daily_price_series_key"),
        CheckConstraint("buy_price_vnd IS NULL OR buy_price_vnd > 0", name="buy_price_positive"),
        CheckConstraint("sell_price_vnd IS NULL OR sell_price_vnd > 0", name="sell_price_positive"),
        CheckConstraint(
            "retail_price_vnd IS NULL OR retail_price_vnd > 0",
            name="retail_price_positive",
        ),
        CheckConstraint("reference_price_vnd > 0", name="reference_price_positive"),
        CheckConstraint(
            "("
            "(buy_price_vnd IS NOT NULL AND sell_price_vnd IS NOT NULL AND retail_price_vnd IS NULL)"
            " OR "
            "(buy_price_vnd IS NULL AND sell_price_vnd IS NULL AND retail_price_vnd IS NOT NULL)"
            ")",
            name="pricing_shape",
        ),
        CheckConstraint("series_origin IN ('BACKFILL', 'LIVE_ROLLUP')", name="series_origin"),
        Index("idx_daily_price_series_lookup", "commodity_id", "location_id", text("price_date DESC")),
        Index(
            "idx_daily_price_series_adj",
            "commodity_id",
            "location_id",
            text("price_date DESC"),
            postgresql_where=text("is_adjustment_day = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodities.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    price_date: Mapped[date] = mapped_column(Date, nullable=False)
    buy_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retail_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reference_price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    series_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    is_adjustment_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_records.id", ondelete="SET NULL"), nullable=True
    )
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    commodity: Mapped[Commodity] = relationship()
    location: Mapped[Location] = relationship()
    source_record: Mapped[PriceRecord | None] = relationship()


class MacroIndicator(Base):
    """Daily macroeconomic and FX series used as exogenous forecasting features."""

    __tablename__ = "macro_indicators"
    __table_args__ = (
        UniqueConstraint(
            "indicator_code", "indicator_date", name="uq_macro_indicators_code_date"
        ),
        CheckConstraint(
            "indicator_code IN ('XAU_USD', 'BRENT_USD', 'WTI_USD', 'DXY', 'USD_VND_VCB')",
            name="indicator_code",
        ),
        CheckConstraint("value > 0", name="value_positive"),
        Index("idx_macro_indicators_code_date_desc", "indicator_code", text("indicator_date DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    indicator_code: Mapped[str] = mapped_column(String(32), nullable=False)
    indicator_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    open_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    high_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    low_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    change_pct_1d: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MarketNewsSignal(Base):
    """Deduplicated RSS financial/commodity news signals scored with lexicon + optional Gemini."""

    __tablename__ = "market_news_signals"
    __table_args__ = (
        UniqueConstraint("url_sha256", name="uq_market_news_signals_url_sha256"),
        UniqueConstraint("title_dedup_key", name="uq_market_news_signals_title_dedup_key"),
        CheckConstraint("domain IN ('GOLD', 'FUEL', 'GAS', 'MACRO')", name="domain"),
        CheckConstraint(
            "polarity_score >= -1.0 AND polarity_score <= 1.0", name="polarity_range"
        ),
        CheckConstraint("impact_level IN ('LOW', 'MEDIUM', 'HIGH')", name="impact_level"),
        CheckConstraint(
            "scoring_engine IN ('LEXICON_V1', 'LEXICON_PLUS_GEMINI')",
            name="scoring_engine",
        ),
        Index("idx_market_news_domain_pub_desc", "domain", text("published_at DESC")),
        Index("idx_market_news_impact_pub_desc", "impact_level", text("published_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    publisher: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    title_dedup_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    polarity_score: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    impact_level: Mapped[str] = mapped_column(String(12), nullable=False)
    catalysts: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    matched_keywords: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    llm_summary_vi: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoring_engine: Mapped[str] = mapped_column(String(24), nullable=False)


class PriceForecast(Base):
    """Immutable pre-target price forecast snapshot with walk-forward diagnostics."""

    __tablename__ = "price_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "commodity_id",
            "location_id",
            "forecast_date",
            "horizon_type",
            "model_version",
            name="uq_price_forecasts_snapshot",
        ),
        CheckConstraint(
            "horizon_type IN ('T_PLUS_1', 'T_PLUS_7', 'NEXT_FUEL_CYCLE', 'NEXT_GAS_MONTH')",
            name="horizon_type",
        ),
        CheckConstraint("status IN ('READY', 'INSUFFICIENT_DATA')", name="status"),
        CheckConstraint(
            "predicted_direction IN ('UP', 'DOWN', 'FLAT', 'UNKNOWN')",
            name="predicted_direction",
        ),
        CheckConstraint(
            "target_date > forecast_date OR (horizon_type = 'NEXT_FUEL_CYCLE' AND target_date >= forecast_date)",
            name="target_after_forecast",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="confidence_range",
        ),
        CheckConstraint(
            "("
            "(status = 'INSUFFICIENT_DATA' AND point_estimate_vnd IS NULL)"
            " OR "
            "(status = 'READY' AND point_estimate_vnd IS NOT NULL AND point_estimate_vnd > 0 "
            "AND lower_bound_vnd IS NOT NULL AND upper_bound_vnd IS NOT NULL "
            "AND lower_bound_vnd <= point_estimate_vnd AND point_estimate_vnd <= upper_bound_vnd)"
            ")",
            name="status_payload_shape",
        ),
        Index(
            "idx_price_forecasts_latest",
            "commodity_id",
            "location_id",
            "horizon_type",
            text("forecast_date DESC"),
        ),
        Index(
            "idx_price_forecasts_pending_eval",
            text("target_date ASC"),
            "status",
            postgresql_where=text("status = 'READY'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodities.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_type: Mapped[str] = mapped_column(String(24), nullable=False)
    model_version: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    baseline_price_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    point_estimate_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lower_bound_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    upper_bound_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_change_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 3), nullable=True)
    predicted_direction: Mapped[str] = mapped_column(String(12), nullable=False)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)
    news_adjustment_pct: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, server_default=text("0")
    )
    insufficient_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    drivers_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    commodity: Mapped[Commodity] = relationship()
    location: Mapped[Location] = relationship()


class ForecastEvaluation(Base):
    """Out-of-sample evaluation comparing stored PriceForecast snapshots vs realized prices."""

    __tablename__ = "forecast_evaluations"
    __table_args__ = (
        UniqueConstraint("forecast_id", name="uq_forecast_evaluations_forecast_id"),
        CheckConstraint("actual_price_vnd > 0", name="actual_price_positive"),
        CheckConstraint("predicted_price_vnd > 0", name="predicted_price_positive"),
        CheckConstraint("abs_error_vnd >= 0", name="abs_error_non_negative"),
        CheckConstraint("pct_error >= 0", name="pct_error_non_negative"),
        CheckConstraint("predicted_direction IN ('UP', 'DOWN', 'FLAT')", name="predicted_direction"),
        CheckConstraint("actual_direction IN ('UP', 'DOWN', 'FLAT')", name="actual_direction"),
        Index(
            "idx_forecast_evaluations_target_desc",
            text("target_date DESC"),
            text("evaluated_at DESC"),
        ),
        Index(
            "idx_forecast_evaluations_group",
            "commodity_id",
            "horizon_type",
            text("target_date DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    forecast_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_forecasts.id", ondelete="CASCADE"), nullable=False
    )
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodities.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    horizon_type: Mapped[str] = mapped_column(String(24), nullable=False)
    model_version: Mapped[str] = mapped_column(String(48), nullable=False)
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_date: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    predicted_price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    abs_error_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pct_error: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    predicted_direction: Mapped[str] = mapped_column(String(12), nullable=False)
    actual_direction: Mapped[str] = mapped_column(String(12), nullable=False)
    direction_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    within_interval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    forecast: Mapped[PriceForecast] = relationship()
    commodity: Mapped[Commodity] = relationship()
    location: Mapped[Location] = relationship()

