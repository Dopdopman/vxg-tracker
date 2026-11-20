"""Audit transparency, run logs, source health, and anomaly alert endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.deps import get_db_session
from vxg_api.errors import ErrorResponse
from vxg_api.schemas.audit import (
    AnomaliesResponse,
    AuditSummaryResponse,
    CrawlRunDetailResponse,
    CrawlRunsListResponse,
    SourcesHealthResponse,
)
from vxg_api.schemas.forecasts import (
    AuditForecastsResponse,
    ForecastEvaluationTypeFilter,
)
from vxg_api.services.audit_query import (
    get_audit_summary,
    get_crawl_run_detail,
    get_sources_health,
    list_anomalies,
    list_crawl_runs,
)
from vxg_api.services.forecast_query import get_audit_forecasts, parse_forecast_horizon
from vxg_shared.enums import AlertStatus, CommodityCategory, CrawlRunStatus, CrawlScope

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit/summary", response_model=AuditSummaryResponse)
async def read_audit_summary(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuditSummaryResponse:
    """Return aggregated audit metrics for today (Asia/Ho_Chi_Minh) and last 7 days."""
    return await get_audit_summary(session)


@router.get(
    "/audit/runs",
    response_model=CrawlRunsListResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_crawl_runs(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    scope: Annotated[CrawlScope | None, Query()] = None,
    status: Annotated[CrawlRunStatus | None, Query()] = None,
) -> CrawlRunsListResponse:
    """Return paginated CrawlRun history sorted newest-first."""
    return await list_crawl_runs(
        session,
        limit=limit,
        offset=offset,
        scope=scope,
        run_status=status,
    )


@router.get(
    "/audit/runs/{run_id}",
    response_model=CrawlRunDetailResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def read_crawl_run_detail(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CrawlRunDetailResponse:
    """Return detailed per-source CrawlSourceLog telemetry for one CrawlRun."""
    return await get_crawl_run_detail(session, run_id)


@router.get("/sources/health", response_model=SourcesHealthResponse)
async def read_sources_health(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SourcesHealthResponse:
    """Return 7-day success rate, latency, and health status for all active sources."""
    return await get_sources_health(session)


@router.get(
    "/audit/anomalies",
    response_model=AnomaliesResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_anomalies(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    status: Annotated[
        AlertStatus | None,
        Query(description="Optional filter: OPEN, ACKNOWLEDGED, or RESOLVED."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnomaliesResponse:
    """Return guardrail and crawler anomaly alerts."""
    return await list_anomalies(
        session,
        alert_status=status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/audit/forecasts",
    response_model=AuditForecastsResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_audit_forecasts(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    evaluation_type: Annotated[
        ForecastEvaluationTypeFilter,
        Query(description="Filter rows by ALL, LIVE (out-of-sample), or BACKTEST (walk-forward)."),
    ] = ForecastEvaluationTypeFilter.ALL,
    domain: Annotated[
        CommodityCategory | None,
        Query(description="Optional domain filter (GOLD, FUEL, GAS)."),
    ] = None,
    horizon: Annotated[
        str | None,
        Query(description="Optional horizon filter (T_PLUS_1, T_PLUS_7, NEXT_FUEL_CYCLE, NEXT_GAS_MONTH)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditForecastsResponse:
    """Return transparent forecast accuracy telemetry strictly separating LIVE vs BACKTEST."""
    parsed_horizon = parse_forecast_horizon(horizon)
    return await get_audit_forecasts(
        session,
        evaluation_type=evaluation_type,
        domain=domain,
        horizon=parsed_horizon,
        limit=limit,
        offset=offset,
    )

