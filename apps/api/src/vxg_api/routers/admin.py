"""Protected admin router for manual crawl job queueing."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.config import ApiSettings
from vxg_api.deps import get_db_session, require_admin_api_key
from vxg_api.errors import ErrorResponse
from vxg_api.schemas.admin import (
    TriggerCrawlRunRequest,
    TriggerCrawlRunResponse,
    TriggerForecastRunRequest,
    TriggerForecastRunResponse,
)
from vxg_api.services.admin_service import trigger_crawl_run, trigger_forecast_run
from vxg_shared.enums import CrawlScope

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post(
    "/crawl-runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerCrawlRunResponse,
    dependencies=[Depends(require_admin_api_key)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_manual_crawl_run(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payload: Annotated[TriggerCrawlRunRequest | None, Body()] = None,
) -> TriggerCrawlRunResponse:
    """Queue a manual CrawlRun for worker pickup or GitHub Actions dispatch."""
    scope = payload.scope if payload is not None else CrawlScope.ALL
    settings: ApiSettings = request.app.state.settings
    http_client: httpx.AsyncClient | None = getattr(request.app.state, "dispatch_http_client", None)
    return await trigger_crawl_run(
        session,
        scope=scope,
        settings=settings,
        http_client=http_client,
    )


@router.post(
    "/forecast-runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerForecastRunResponse,
    dependencies=[Depends(require_admin_api_key)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_manual_forecast_run(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payload: Annotated[TriggerForecastRunRequest | None, Body()] = None,
) -> TriggerForecastRunResponse:
    """Queue a manual ForecastRun (isolated from CrawlRun) for worker or GitHub Actions."""
    scope = payload.scope if payload is not None else CrawlScope.ALL
    settings: ApiSettings = request.app.state.settings
    http_client: httpx.AsyncClient | None = getattr(request.app.state, "dispatch_http_client", None)
    return await trigger_forecast_run(
        session,
        scope=scope,
        settings=settings,
        http_client=http_client,
    )
