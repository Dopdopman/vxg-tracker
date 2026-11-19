"""APScheduler setup locked strictly to Asia/Ho_Chi_Minh (UTC+7)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vxg_shared.enums import CrawlScope, TriggerType
from vxg_shared.timezone import VN_TZ
from vxg_worker.runner import enqueue_crawl_run
from vxg_worker.storage.snapshots import prune_expired_snapshots

logger = logging.getLogger("vxg_worker.scheduler")


def build_worker_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    retention_days: int = 90,
) -> AsyncIOScheduler:
    """Create AsyncIOScheduler locked to Asia/Ho_Chi_Minh for crawls & retention cleanup."""
    scheduler = AsyncIOScheduler(
        timezone=VN_TZ,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )

    async def _enqueue(scope: CrawlScope) -> None:
        run = await enqueue_crawl_run(
            session_factory,
            scope=scope,
            trigger_type=TriggerType.SCHEDULED,
        )
        logger.info("Scheduled job enqueued: run_code=%s scope=%s", run.run_code, scope.value)

    async def _prune_snapshots() -> None:
        async with session_factory() as session:
            result = await prune_expired_snapshots(session, retention_days=retention_days)
            await session.commit()
        logger.info(
            "Snapshot retention cleanup finished: retention_days=%d deleted=%d freed_bytes=%d",
            result.retention_days,
            result.deleted_count,
            result.freed_bytes,
        )

    schedule_specs: tuple[tuple[str, CrawlScope, CronTrigger], ...] = (
        (
            "gold_open_0845",
            CrawlScope.GOLD,
            CronTrigger(day_of_week="mon-sat", hour=8, minute=45, timezone=VN_TZ),
        ),
        (
            "gold_intraday_1130_1430",
            CrawlScope.GOLD,
            CronTrigger(day_of_week="mon-sat", hour="11,14", minute=30, timezone=VN_TZ),
        ),
        (
            "gold_close_1700",
            CrawlScope.GOLD,
            CronTrigger(day_of_week="mon-sat", hour=17, minute=0, timezone=VN_TZ),
        ),
        (
            "gold_sunday_0900",
            CrawlScope.GOLD,
            CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=VN_TZ),
        ),
        (
            "fuel_daily_0705",
            CrawlScope.FUEL,
            CronTrigger(hour=7, minute=5, timezone=VN_TZ),
        ),
        (
            "fuel_thursday_adjust",
            CrawlScope.FUEL,
            CronTrigger(day_of_week="thu", hour=15, minute="15,30", timezone=VN_TZ),
        ),
        (
            "fuel_thursday_1600",
            CrawlScope.FUEL,
            CronTrigger(day_of_week="thu", hour=16, minute=0, timezone=VN_TZ),
        ),
        (
            "gas_daily_0730",
            CrawlScope.GAS,
            CronTrigger(hour=7, minute=30, timezone=VN_TZ),
        ),
        (
            "gas_monthly_first",
            CrawlScope.GAS,
            CronTrigger(day="1,2", hour="6,8,10", minute=30, timezone=VN_TZ),
        ),
    )

    for job_id, scope, trigger in schedule_specs:
        scheduler.add_job(
            _enqueue,
            trigger=trigger,
            id=job_id,
            args=[scope],
            replace_existing=True,
        )

    scheduler.add_job(
        _prune_snapshots,
        trigger=CronTrigger(hour=3, minute=15, timezone=VN_TZ),
        id="snapshot_retention_0315",
        replace_existing=True,
    )

    return scheduler
