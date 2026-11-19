"""Query services for audit telemetry, run logs, source health, and anomalies."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import status
from sqlalchemy import RowMapping, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.errors import AppError
from vxg_api.schemas.audit import (
    AnomaliesResponse,
    AnomalyAlertItem,
    AuditSummaryResponse,
    AuditWindowMetrics,
    CrawlRunDetailResponse,
    CrawlRunItem,
    CrawlRunsListResponse,
    CrawlSourceLogItem,
    SourceHealthItem,
    SourceHealthStatus,
    SourcesHealthResponse,
)
from vxg_shared.db.models import (
    AnomalyAlert,
    CrawlRun,
    CrawlSourceLog,
    RawEvidenceSnapshot,
    Source,
)
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
from vxg_shared.timezone import VN_TZ, VN_TZ_NAME, vn_now

_HEALTH_WINDOW_DAYS = 7


def _window_bounds() -> tuple[datetime, date, datetime, datetime]:
    now_vn = vn_now()
    today_date = now_vn.date()
    today_start = datetime.combine(today_date, time.min, tzinfo=VN_TZ)
    last_7_days_start = datetime.combine(today_date - timedelta(days=6), time.min, tzinfo=VN_TZ)
    return now_vn, today_date, today_start, last_7_days_start


def _to_crawl_run_item(run: CrawlRun) -> CrawlRunItem:
    return CrawlRunItem(
        id=run.id,
        run_code=run.run_code,
        scope_category=CrawlScope(run.scope_category),
        trigger_type=TriggerType(run.trigger_type),
        status=CrawlRunStatus(run.status),
        sources_queried=run.sources_queried_count,
        pages_fetched_ok=run.pages_fetched_success,
        pages_fetched_failed=run.pages_fetched_failed,
        documents_parsed=run.documents_parsed_count,
        records_extracted=run.records_extracted_count,
        records_verified=run.records_verified_count,
        records_flagged=run.records_flagged_count,
        records_unchanged=run.records_unchanged_count,
        started_at_vn=run.started_at,
        finished_at_vn=run.finished_at,
        duration_ms=run.duration_ms,
        error_summary=run.error_summary,
    )


async def _aggregate_run_window(
    session: AsyncSession,
    window_start: datetime,
) -> AuditWindowMetrics:
    stmt = select(
        func.count(CrawlRun.id).label("total_runs"),
        func.coalesce(
            func.sum(case((CrawlRun.status == CrawlRunStatus.COMPLETED.value, 1), else_=0)),
            0,
        ).label("completed_runs"),
        func.coalesce(
            func.sum(case((CrawlRun.status == CrawlRunStatus.PARTIAL_SUCCESS.value, 1), else_=0)),
            0,
        ).label("partial_success_runs"),
        func.coalesce(
            func.sum(case((CrawlRun.status == CrawlRunStatus.FAILED.value, 1), else_=0)),
            0,
        ).label("failed_runs"),
        func.coalesce(
            func.sum(
                case(
                    (
                        CrawlRun.status.in_(
                            (CrawlRunStatus.QUEUED.value, CrawlRunStatus.RUNNING.value)
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("active_runs"),
        func.coalesce(func.sum(CrawlRun.sources_queried_count), 0).label("sources_queried"),
        func.coalesce(func.sum(CrawlRun.pages_fetched_success), 0).label("pages_fetched_ok"),
        func.coalesce(func.sum(CrawlRun.pages_fetched_failed), 0).label("pages_fetched_failed"),
        func.coalesce(func.sum(CrawlRun.documents_parsed_count), 0).label("documents_parsed"),
        func.coalesce(func.sum(CrawlRun.records_extracted_count), 0).label("records_extracted"),
        func.coalesce(func.sum(CrawlRun.records_verified_count), 0).label("records_verified"),
        func.coalesce(func.sum(CrawlRun.records_flagged_count), 0).label("records_flagged"),
        func.coalesce(func.sum(CrawlRun.records_unchanged_count), 0).label("records_unchanged"),
    ).where(CrawlRun.started_at >= window_start)

    row: RowMapping = (await session.execute(stmt)).mappings().one()
    return AuditWindowMetrics(
        window_start_vn=window_start,
        total_runs=int(row["total_runs"] or 0),
        completed_runs=int(row["completed_runs"] or 0),
        partial_success_runs=int(row["partial_success_runs"] or 0),
        failed_runs=int(row["failed_runs"] or 0),
        active_runs=int(row["active_runs"] or 0),
        sources_queried=int(row["sources_queried"] or 0),
        pages_fetched_ok=int(row["pages_fetched_ok"] or 0),
        pages_fetched_failed=int(row["pages_fetched_failed"] or 0),
        documents_parsed=int(row["documents_parsed"] or 0),
        records_extracted=int(row["records_extracted"] or 0),
        records_verified=int(row["records_verified"] or 0),
        records_flagged=int(row["records_flagged"] or 0),
        records_unchanged=int(row["records_unchanged"] or 0),
    )


async def get_audit_summary(session: AsyncSession) -> AuditSummaryResponse:
    """Compute transparency totals for today (Asia/Ho_Chi_Minh) and last 7 days."""
    now_vn, today_date, today_start, last_7_days_start = _window_bounds()
    today_metrics = await _aggregate_run_window(session, today_start)
    last_7_days_metrics = await _aggregate_run_window(session, last_7_days_start)

    active_sources_count = int(
        (
            await session.execute(
                select(func.count(Source.id)).where(Source.is_active.is_(True))
            )
        ).scalar_one()
    )
    open_anomalies = int(
        (
            await session.execute(
                select(func.count(AnomalyAlert.id)).where(
                    AnomalyAlert.status == AlertStatus.OPEN.value
                )
            )
        ).scalar_one()
    )
    latest_run_row = (
        await session.execute(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(1))
    ).scalar_one_or_none()

    return AuditSummaryResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=now_vn,
        today_date_vn=today_date,
        active_sources_count=active_sources_count,
        open_anomalies=open_anomalies,
        today=today_metrics,
        last_7_days=last_7_days_metrics,
        latest_run=_to_crawl_run_item(latest_run_row) if latest_run_row is not None else None,
    )


async def list_crawl_runs(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    scope: CrawlScope | None = None,
    run_status: CrawlRunStatus | None = None,
) -> CrawlRunsListResponse:
    """Return paginated CrawlRun history sorted newest-first."""
    count_stmt = select(func.count(CrawlRun.id))
    data_stmt = select(CrawlRun)
    if scope is not None:
        count_stmt = count_stmt.where(CrawlRun.scope_category == scope.value)
        data_stmt = data_stmt.where(CrawlRun.scope_category == scope.value)
    if run_status is not None:
        count_stmt = count_stmt.where(CrawlRun.status == run_status.value)
        data_stmt = data_stmt.where(CrawlRun.status == run_status.value)

    total = int((await session.execute(count_stmt)).scalar_one())
    runs = (
        await session.execute(
            data_stmt.order_by(CrawlRun.started_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    return CrawlRunsListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_crawl_run_item(run) for run in runs],
    )


async def get_crawl_run_detail(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> CrawlRunDetailResponse:
    """Return one CrawlRun with all associated CrawlSourceLog audit rows."""
    run = await session.get(CrawlRun, run_id)
    if run is None:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="CRAWL_RUN_NOT_FOUND",
            message=f"Crawl run '{run_id}' was not found.",
        )

    rows = (
        await session.execute(
            select(CrawlSourceLog, Source, RawEvidenceSnapshot)
            .join(Source, CrawlSourceLog.source_id == Source.id)
            .outerjoin(RawEvidenceSnapshot, CrawlSourceLog.snapshot_id == RawEvidenceSnapshot.id)
            .where(CrawlSourceLog.crawl_run_id == run_id)
            .order_by(CrawlSourceLog.created_at.asc(), CrawlSourceLog.id.asc())
        )
    ).all()

    base = _to_crawl_run_item(run)
    source_logs = [
        CrawlSourceLogItem(
            id=log.id,
            source_code=source.code,
            source_name=source.name,
            source_category=CommodityCategory(source.category),
            source_tier=SourceTier(source.tier),
            request_url=log.request_url,
            final_url=log.final_url,
            http_status=log.http_status,
            tier_used=log.fetch_strategy_used,
            fetch_status=FetchStatus(log.fetch_status),
            latency_ms=log.response_time_ms,
            attempt_number=log.attempt_number,
            bytes_received=snapshot.byte_size if snapshot is not None else None,
            content_sha256=log.content_sha256,
            documents_parsed=log.documents_parsed,
            records_extracted=log.records_extracted,
            records_verified=log.records_verified,
            records_flagged=log.records_flagged,
            error_code=log.error_code,
            error_message=log.error_message,
            parser_metadata=dict(log.parser_metadata),
            created_at_vn=log.created_at,
        )
        for log, source, snapshot in rows
    ]

    return CrawlRunDetailResponse(
        **base.model_dump(),
        source_logs=source_logs,
    )


def _derive_source_health_status(
    source: Source,
    latest_log: CrawlSourceLog | None,
) -> SourceHealthStatus:
    if source.consecutive_failures >= 3:
        return SourceHealthStatus.DOWN
    if source.consecutive_failures >= 1 or (
        latest_log is not None and latest_log.fetch_status != FetchStatus.SUCCESS.value
    ):
        return SourceHealthStatus.DEGRADED
    if source.last_success_at is not None:
        return SourceHealthStatus.HEALTHY
    return SourceHealthStatus.UNKNOWN


async def get_sources_health(session: AsyncSession) -> SourcesHealthResponse:
    """Return 7-day reliability, latency, and current operational status for active sources."""
    now_vn, _, _, last_7_days_start = _window_bounds()

    sources = list(
        (
            await session.execute(
                select(Source)
                .where(Source.is_active.is_(True))
                .order_by(Source.category.asc(), Source.tier.asc(), Source.id.asc())
            )
        )
        .scalars()
        .all()
    )
    active_ids = [source.id for source in sources]

    stats_by_source: dict[int, RowMapping] = {}
    latest_log_by_source: dict[int, CrawlSourceLog] = {}

    if active_ids:
        agg_rows = (
            await session.execute(
                select(
                    CrawlSourceLog.source_id.label("source_id"),
                    func.count(CrawlSourceLog.id).label("total_checks"),
                    func.coalesce(
                        func.sum(
                            case(
                                (CrawlSourceLog.fetch_status == FetchStatus.SUCCESS.value, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("success_checks"),
                    func.avg(CrawlSourceLog.response_time_ms).label("avg_latency_ms"),
                )
                .where(
                    CrawlSourceLog.source_id.in_(active_ids),
                    CrawlSourceLog.created_at >= last_7_days_start,
                )
                .group_by(CrawlSourceLog.source_id)
            )
        ).mappings().all()
        stats_by_source = {int(row["source_id"]): row for row in agg_rows}

        latest_logs = (
            await session.execute(
                select(CrawlSourceLog)
                .where(CrawlSourceLog.source_id.in_(active_ids))
                .distinct(CrawlSourceLog.source_id)
                .order_by(CrawlSourceLog.source_id.asc(), CrawlSourceLog.created_at.desc())
            )
        ).scalars().all()
        latest_log_by_source = {log.source_id: log for log in latest_logs}

    items: list[SourceHealthItem] = []
    counts: dict[SourceHealthStatus, int] = {
        SourceHealthStatus.HEALTHY: 0,
        SourceHealthStatus.DEGRADED: 0,
        SourceHealthStatus.DOWN: 0,
        SourceHealthStatus.UNKNOWN: 0,
    }

    for source in sources:
        stat = stats_by_source.get(source.id)
        latest_log = latest_log_by_source.get(source.id)

        total_checks = int(stat["total_checks"] or 0) if stat is not None else 0
        success_checks = int(stat["success_checks"] or 0) if stat is not None else 0
        failed_checks = max(0, total_checks - success_checks)
        success_rate_pct = (
            round((success_checks / total_checks) * 100.0, 2) if total_checks > 0 else None
        )
        raw_avg_latency: Any = stat["avg_latency_ms"] if stat is not None else None
        avg_latency_ms = round(float(raw_avg_latency)) if raw_avg_latency is not None else None

        health_status = _derive_source_health_status(source, latest_log)
        counts[health_status] += 1

        items.append(
            SourceHealthItem(
                source_code=source.code,
                source_name=source.name,
                category=CommodityCategory(source.category),
                tier=SourceTier(source.tier),
                endpoint_url=source.endpoint_url,
                fetch_strategy=FetchStrategy(source.fetch_strategy),
                schedule_cron=source.schedule_cron,
                current_status=health_status,
                consecutive_failures=source.consecutive_failures,
                total_checks_7d=total_checks,
                success_checks_7d=success_checks,
                failed_checks_7d=failed_checks,
                success_rate_7d_pct=success_rate_pct,
                avg_latency_ms_7d=avg_latency_ms,
                last_success_at_vn=source.last_success_at,
                last_checked_at_vn=latest_log.created_at if latest_log is not None else None,
                last_fetch_status=(
                    FetchStatus(latest_log.fetch_status) if latest_log is not None else None
                ),
                last_http_status=latest_log.http_status if latest_log is not None else None,
                last_error_message=latest_log.error_message if latest_log is not None else None,
            )
        )

    return SourcesHealthResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=now_vn,
        window_days=_HEALTH_WINDOW_DAYS,
        total_active_sources=len(items),
        healthy_count=counts[SourceHealthStatus.HEALTHY],
        degraded_count=counts[SourceHealthStatus.DEGRADED],
        down_count=counts[SourceHealthStatus.DOWN],
        unknown_count=counts[SourceHealthStatus.UNKNOWN],
        items=items,
    )


async def list_anomalies(
    session: AsyncSession,
    *,
    alert_status: AlertStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AnomaliesResponse:
    """Return guardrail and crawler anomaly alerts filtered optionally by status."""
    count_stmt = select(func.count(AnomalyAlert.id))
    data_stmt = (
        select(AnomalyAlert, CrawlRun, CrawlSourceLog, Source)
        .outerjoin(CrawlRun, AnomalyAlert.crawl_run_id == CrawlRun.id)
        .outerjoin(CrawlSourceLog, AnomalyAlert.source_log_id == CrawlSourceLog.id)
        .outerjoin(Source, CrawlSourceLog.source_id == Source.id)
    )

    if alert_status is not None:
        count_stmt = count_stmt.where(AnomalyAlert.status == alert_status.value)
        data_stmt = data_stmt.where(AnomalyAlert.status == alert_status.value)

    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(
            data_stmt.order_by(AnomalyAlert.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()

    return AnomaliesResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            AnomalyAlertItem(
                id=alert.id,
                alert_type=AlertType(alert.alert_type),
                severity=AlertSeverity(alert.severity),
                status=AlertStatus(alert.status),
                message=alert.message,
                crawl_run_id=alert.crawl_run_id,
                run_code=crawl_run.run_code if crawl_run is not None else None,
                source_log_id=alert.source_log_id,
                source_code=source.code if source is not None else None,
                source_name=source.name if source is not None else None,
                price_record_id=alert.price_record_id,
                payload=dict(alert.payload),
                created_at_vn=alert.created_at,
                resolved_at_vn=alert.resolved_at,
            )
            for alert, crawl_run, _log, source in rows
        ],
    )
