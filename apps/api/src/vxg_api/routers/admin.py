"""Protected admin router for manual crawl job queueing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.deps import get_db_session, require_admin_api_key
from vxg_api.errors import ErrorResponse
from vxg_api.schemas.admin import TriggerCrawlRunRequest, TriggerCrawlRunResponse
from vxg_api.services.admin_service import trigger_crawl_run
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
    },
)
async def create_manual_crawl_run(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payload: Annotated[TriggerCrawlRunRequest | None, Body()] = None,
) -> TriggerCrawlRunResponse:
    """Queue a manual CrawlRun for asynchronous pickup by vxg-worker."""
    scope = payload.scope if payload is not None else CrawlScope.ALL
    return await trigger_crawl_run(session, scope=scope)
