"""Schemas for /api/v1/audit/* and /api/v1/sources/health endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from vxg_api.schemas.common import VnDatetime
from vxg_shared.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    CommodityCategory,
    CrawlRunStatus,
    CrawlScope,
    FetchStatus,
    FetchStrategy,
    SourceTier,
    TriggerType,
)


class CrawlRunItem(BaseModel):
    """Summary counters and timestamps for a single CrawlRun."""

    id: uuid.UUID
    run_code: str
    scope_category: CrawlScope
    trigger_type: TriggerType
    status: CrawlRunStatus
    sources_queried: int
    pages_fetched_ok: int
    pages_fetched_failed: int
    documents_parsed: int
    records_extracted: int
    records_verified: int
    records_flagged: int
    records_unchanged: int
    started_at_vn: VnDatetime
    finished_at_vn: VnDatetime | None = None
    duration_ms: int | None = None
    error_summary: str | None = None


class AuditWindowMetrics(BaseModel):
    """Aggregated crawl and verification metrics over a time window."""

    window_start_vn: VnDatetime
    total_runs: int
    completed_runs: int
    partial_success_runs: int
    failed_runs: int
    active_runs: int
    sources_queried: int
    pages_fetched_ok: int
    pages_fetched_failed: int
    documents_parsed: int
    records_extracted: int
    records_verified: int
    records_flagged: int
    records_unchanged: int


class AuditSummaryResponse(BaseModel):
    """Operational transparency summary for today (VN) and trailing 7 days."""

    timezone: str
    as_of_vn: VnDatetime
    today_date_vn: date
    active_sources_count: int
    open_anomalies: int
    today: AuditWindowMetrics
    last_7_days: AuditWindowMetrics
    latest_run: CrawlRunItem | None = None


class CrawlRunsListResponse(BaseModel):
    """Paginated list of historical crawl runs."""

    total: int
    limit: int
    offset: int
    items: list[CrawlRunItem]


class CrawlSourceLogItem(BaseModel):
    """Per-source fetch and parse telemetry row inside a CrawlRun."""

    id: uuid.UUID
    source_code: str
    source_name: str
    source_category: CommodityCategory
    source_tier: SourceTier
    request_url: str
    final_url: str | None = None
    http_status: int | None = None
    tier_used: str
    fetch_status: FetchStatus
    latency_ms: int | None = None
    attempt_number: int
    bytes_received: int | None = None
    content_sha256: str | None = None
    documents_parsed: int
    records_extracted: int
    records_verified: int
    records_flagged: int
    error_code: str | None = None
    error_message: str | None = None
    parser_metadata: dict[str, Any]
    created_at_vn: VnDatetime


class CrawlRunDetailResponse(CrawlRunItem):
    """Detailed CrawlRun telemetry including all per-source logs."""

    source_logs: list[CrawlSourceLogItem]


class SourceHealthStatus(StrEnum):
    """Derived operational health indicator for a source."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class SourceHealthItem(BaseModel):
    """Health telemetry and 7-day success rate for one active source."""

    source_code: str
    source_name: str
    category: CommodityCategory
    tier: SourceTier
    endpoint_url: str
    fetch_strategy: FetchStrategy
    schedule_cron: str
    current_status: SourceHealthStatus
    consecutive_failures: int
    total_checks_7d: int
    success_checks_7d: int
    failed_checks_7d: int
    success_rate_7d_pct: float | None = None
    avg_latency_ms_7d: int | None = None
    last_success_at_vn: VnDatetime | None = None
    last_checked_at_vn: VnDatetime | None = None
    last_fetch_status: FetchStatus | None = None
    last_http_status: int | None = None
    last_error_message: str | None = None


class SourcesHealthResponse(BaseModel):
    """Aggregated source health status across all active configured sources."""

    timezone: str
    as_of_vn: VnDatetime
    window_days: int
    total_active_sources: int
    healthy_count: int
    degraded_count: int
    down_count: int
    unknown_count: int
    items: list[SourceHealthItem]


class AnomalyAlertItem(BaseModel):
    """Single anomaly alert raised by guardrails or crawler drift detection."""

    id: uuid.UUID
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    message: str
    crawl_run_id: uuid.UUID | None = None
    run_code: str | None = None
    source_log_id: uuid.UUID | None = None
    source_code: str | None = None
    source_name: str | None = None
    price_record_id: uuid.UUID | None = None
    payload: dict[str, Any]
    created_at_vn: VnDatetime
    resolved_at_vn: VnDatetime | None = None


class AnomaliesResponse(BaseModel):
    """Paginated list of anomaly alerts."""

    total: int
    limit: int
    offset: int
    items: list[AnomalyAlertItem]

