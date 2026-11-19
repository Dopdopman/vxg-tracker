"""Admin command service to safely enqueue CrawlRun jobs without spam overlaps."""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.errors import AppError
from vxg_api.schemas.admin import TriggerCrawlRunResponse
from vxg_api.schemas.common import to_vn_iso
from vxg_shared.db.models import CrawlRun
from vxg_shared.enums import CrawlRunStatus, CrawlScope, TriggerType
from vxg_shared.timezone import format_vn_run_code, vn_now

# Distinct PG transaction-level advisory lock key to serialize admin enqueue requests.
_ADMIN_ENQUEUE_XACT_LOCK_KEY = 908_712_002

_ACTIVE_RUN_STATUSES: tuple[str, ...] = (
    CrawlRunStatus.QUEUED.value,
    CrawlRunStatus.RUNNING.value,
)


def _scopes_overlap(requested: CrawlScope, active_scope: str) -> bool:
    if requested == CrawlScope.ALL or active_scope == CrawlScope.ALL.value:
        return True
    return requested.value == active_scope


async def trigger_crawl_run(
    session: AsyncSession,
    *,
    scope: CrawlScope,
) -> TriggerCrawlRunResponse:
    """Enqueue a manual crawl run or reject with 409 Conflict if an overlapping run is active."""
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

    for active in active_runs:
        if _scopes_overlap(scope, active.scope_category):
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

    return TriggerCrawlRunResponse(
        run_id=run.id,
        run_code=run.run_code,
        scope=CrawlScope(run.scope_category),
        trigger_type=TriggerType(run.trigger_type),
        status=CrawlRunStatus(run.status),
        queued_at_vn=run.started_at,
    )

