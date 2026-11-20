"""Admin command service to enqueue CrawlRun jobs and optionally dispatch GitHub Actions."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta

import httpx
from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.config import ApiSettings, WorkerDispatchMode
from vxg_api.errors import AppError
from vxg_api.schemas.admin import TriggerCrawlRunResponse, TriggerForecastRunResponse
from vxg_api.schemas.common import to_vn_iso
from vxg_shared.db.models import CrawlRun, ForecastRun
from vxg_shared.enums import CrawlRunStatus, CrawlScope, TriggerType
from vxg_shared.timezone import format_vn_run_code, to_vn_tz, vn_now

logger = logging.getLogger("vxg_api.admin")

# Distinct PG transaction-level advisory lock keys to serialize admin enqueue requests.
_ADMIN_ENQUEUE_XACT_LOCK_KEY = 908_712_002
_ADMIN_FORECAST_XACT_LOCK_KEY = 908_712_006

_ACTIVE_RUN_STATUSES: tuple[str, ...] = (
    CrawlRunStatus.QUEUED.value,
    CrawlRunStatus.RUNNING.value,
)
_STALE_QUEUED_TIMEOUT = timedelta(minutes=15)
_STALE_QUEUED_ERROR_SUMMARY = "Timed out waiting in QUEUED state (>15m)"


class WorkflowDispatchError(Exception):
    """Raised when dispatching a workflow_dispatch event to GitHub Actions fails."""

    def __init__(self, message: str, *, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.upstream_status = upstream_status


def _scopes_overlap(requested: CrawlScope, active_scope: str) -> bool:
    if requested == CrawlScope.ALL or active_scope == CrawlScope.ALL.value:
        return True
    return requested.value == active_scope


async def dispatch_github_workflow(
    *,
    settings: ApiSettings,
    run_id: uuid.UUID,
    scope: CrawlScope,
    task_type: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Trigger GitHub Actions workflow_dispatch passing queued run_id and scope."""
    repo = settings.github_repo.strip()
    workflow_file = settings.github_workflow_file.strip() or "crawl-schedule.yml"
    ref = settings.github_workflow_ref.strip() or "main"
    token = settings.github_dispatch_token.strip()

    if "/" not in repo or not token:
        raise WorkflowDispatchError(
            "Missing GITHUB_REPO ('owner/repo') or GITHUB_DISPATCH_TOKEN configuration."
        )

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vxg-tracker-api/0.1",
    }
    inputs: dict[str, str] = {
        "scope": scope.value,
        "run_id": str(run_id),
    }
    if task_type is not None:
        inputs["task_type"] = task_type
    payload = {
        "ref": ref,
        "inputs": inputs,
    }

    async def _post(client: httpx.AsyncClient) -> None:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise WorkflowDispatchError(
                f"Network error contacting GitHub Actions API: {exc}"
            ) from exc

        if resp.status_code not in (200, 201, 202, 204):
            detail = resp.text.strip()[:300] or f"HTTP {resp.status_code}"
            raise WorkflowDispatchError(
                f"GitHub Actions API returned HTTP {resp.status_code}: {detail}",
                upstream_status=resp.status_code,
            )

    if http_client is not None:
        await _post(http_client)
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _post(client)


async def trigger_crawl_run(
    session: AsyncSession,
    *,
    scope: CrawlScope,
    settings: ApiSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TriggerCrawlRunResponse:
    """Enqueue a manual crawl run, and dispatch GitHub Actions when configured."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _ADMIN_ENQUEUE_XACT_LOCK_KEY},
    )

    active_runs = (
        await session.execute(
            select(CrawlRun)
            .where(CrawlRun.status.in_(_ACTIVE_RUN_STATUSES))
            .order_by(CrawlRun.started_at.desc())
        )
    ).scalars().all()

    now_vn = vn_now()
    stale_cutoff = now_vn - _STALE_QUEUED_TIMEOUT
    expired_any = False
    remaining_active_runs: list[CrawlRun] = []

    for active in active_runs:
        started_vn = to_vn_tz(active.started_at)
        if active.status == CrawlRunStatus.QUEUED.value and started_vn < stale_cutoff:
            active.status = CrawlRunStatus.FAILED.value
            active.finished_at = now_vn
            active.duration_ms = max(0, int((now_vn - started_vn).total_seconds() * 1000))
            active.error_summary = _STALE_QUEUED_ERROR_SUMMARY
            expired_any = True
            logger.warning(
                "Auto-expired stale QUEUED crawl_run run_code=%s scope=%s started_at=%s",
                active.run_code,
                active.scope_category,
                started_vn.isoformat(),
            )
            continue
        remaining_active_runs.append(active)

    for active in remaining_active_runs:
        if _scopes_overlap(scope, active.scope_category):
            if expired_any:
                await session.commit()
            raise AppError(
                status_code=status.HTTP_409_CONFLICT,
                code="CRAWL_RUN_ALREADY_ACTIVE",
                message=(
                    f"A crawl run overlapping scope '{scope.value}' is already {active.status}."
                ),
                details={
                    "active_run_id": str(active.id),
                    "active_run_code": active.run_code,
                    "active_scope": active.scope_category,
                    "active_status": active.status,
                    "started_at_vn": to_vn_iso(active.started_at),
                },
            )

    mono_start = time.monotonic()
    queued_at = vn_now()
    run = CrawlRun(
        run_code=format_vn_run_code(scope.value, dt=queued_at),
        scope_category=scope.value,
        trigger_type=TriggerType.MANUAL_ADMIN.value,
        status=CrawlRunStatus.QUEUED.value,
        started_at=queued_at,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    if settings is not None and settings.dispatch_mode == WorkerDispatchMode.GITHUB_ACTIONS:
        try:
            await dispatch_github_workflow(
                settings=settings,
                run_id=run.id,
                scope=scope,
                http_client=http_client,
            )
            logger.info(
                "Dispatched GitHub Actions workflow for run_code=%s scope=%s",
                run.run_code,
                scope.value,
            )
        except WorkflowDispatchError as exc:
            logger.error(
                "Failed GitHub Actions dispatch for run_code=%s: %s",
                run.run_code,
                exc,
            )
            run.status = CrawlRunStatus.FAILED.value
            run.finished_at = vn_now()
            run.duration_ms = int((time.monotonic() - mono_start) * 1000)
            run.error_summary = f"GitHub Actions dispatch failed: {exc}"[:2000]
            await session.commit()
            raise AppError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="GITHUB_DISPATCH_FAILED",
                message=(
                    "Failed to dispatch crawl workflow to GitHub Actions; "
                    "crawl run marked as FAILED."
                ),
                details={
                    "run_id": str(run.id),
                    "run_code": run.run_code,
                    "upstream_status": exc.upstream_status,
                    "reason": str(exc),
                },
            ) from exc

    return TriggerCrawlRunResponse(
        run_id=run.id,
        run_code=run.run_code,
        scope=CrawlScope(run.scope_category),
        trigger_type=TriggerType(run.trigger_type),
        status=CrawlRunStatus(run.status),
        queued_at_vn=run.started_at,
    )


async def trigger_forecast_run(
    session: AsyncSession,
    *,
    scope: CrawlScope,
    settings: ApiSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TriggerForecastRunResponse:
    """Enqueue a manual ForecastRun (isolated from CrawlRun) and optionally dispatch GitHub Actions."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _ADMIN_FORECAST_XACT_LOCK_KEY},
    )

    active_runs = (
        await session.execute(
            select(ForecastRun)
            .where(ForecastRun.status.in_(_ACTIVE_RUN_STATUSES))
            .order_by(ForecastRun.started_at.desc())
        )
    ).scalars().all()

    now_vn = vn_now()
    stale_cutoff = now_vn - _STALE_QUEUED_TIMEOUT
    expired_any = False
    remaining_active_runs: list[ForecastRun] = []

    for active in active_runs:
        started_vn = to_vn_tz(active.started_at)
        if active.status == CrawlRunStatus.QUEUED.value and started_vn < stale_cutoff:
            active.status = CrawlRunStatus.FAILED.value
            active.finished_at = now_vn
            active.duration_ms = max(0, int((now_vn - started_vn).total_seconds() * 1000))
            active.error_summary = _STALE_QUEUED_ERROR_SUMMARY
            expired_any = True
            continue
        remaining_active_runs.append(active)

    for active in remaining_active_runs:
        if _scopes_overlap(scope, active.scope_category):
            if expired_any:
                await session.commit()
            raise AppError(
                status_code=status.HTTP_409_CONFLICT,
                code="FORECAST_RUN_ALREADY_ACTIVE",
                message=(
                    f"A forecast run overlapping scope '{scope.value}' is already {active.status}."
                ),
                details={
                    "active_run_id": str(active.id),
                    "active_run_code": active.run_code,
                    "active_scope": active.scope_category,
                    "active_status": active.status,
                    "started_at_vn": to_vn_iso(active.started_at),
                },
            )

    mono_start = time.monotonic()
    queued_at = vn_now()
    run = ForecastRun(
        run_code=f"FC-{format_vn_run_code(scope.value, dt=queued_at)}",
        scope_category=scope.value,
        trigger_type=TriggerType.MANUAL_ADMIN.value,
        status=CrawlRunStatus.QUEUED.value,
        started_at=queued_at,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    if settings is not None and settings.dispatch_mode == WorkerDispatchMode.GITHUB_ACTIONS:
        try:
            await dispatch_github_workflow(
                settings=settings,
                run_id=run.id,
                scope=scope,
                task_type="forecast",
                http_client=http_client,
            )
            logger.info(
                "Dispatched GitHub Actions forecast workflow for run_code=%s scope=%s",
                run.run_code,
                scope.value,
            )
        except WorkflowDispatchError as exc:
            logger.error(
                "Failed GitHub Actions forecast dispatch for run_code=%s: %s",
                run.run_code,
                exc,
            )
            run.status = CrawlRunStatus.FAILED.value
            run.finished_at = vn_now()
            run.duration_ms = int((time.monotonic() - mono_start) * 1000)
            run.error_summary = f"GitHub Actions dispatch failed: {exc}"[:2000]
            await session.commit()
            raise AppError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="GITHUB_DISPATCH_FAILED",
                message=(
                    "Failed to dispatch forecast workflow to GitHub Actions; "
                    "forecast run marked as FAILED."
                ),
                details={
                    "run_id": str(run.id),
                    "run_code": run.run_code,
                    "upstream_status": exc.upstream_status,
                    "reason": str(exc),
                },
            ) from exc

    return TriggerForecastRunResponse(
        run_id=run.id,
        run_code=run.run_code,
        scope=CrawlScope(run.scope_category),
        trigger_type=TriggerType(run.trigger_type),
        status=CrawlRunStatus(run.status),
        queued_at_vn=run.started_at,
    )
