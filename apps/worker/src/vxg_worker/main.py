"""Worker daemon entrypoint combining APScheduler (UTC+7) & QUEUED run polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from sqlalchemy import func, select, text

from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import Source
from vxg_shared.timezone import VN_TZ_NAME, vn_now
from vxg_worker.config import WorkerSettings
from vxg_worker.forecasting.pipeline import poll_queued_forecast_run
from vxg_worker.runner import CrawlRunner
from vxg_worker.scheduler import build_worker_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vxg_worker")


async def run_worker_daemon() -> None:
    """Boot scheduler (Asia/Ho_Chi_Minh) and poll QUEUED crawl_runs until stopped."""
    settings = WorkerSettings()
    engine = create_async_db_engine(settings.async_database_url)
    session_factory = create_session_factory(engine)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    runner = CrawlRunner(
        engine=engine,
        session_factory=session_factory,
        settings=settings,
    )
    scheduler = build_worker_scheduler(
        session_factory,
        retention_days=settings.snapshot_retention_days,
    )

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            active_sources = int(
                (
                    await session.execute(
                        select(func.count()).select_from(Source).where(Source.is_active.is_(True))
                    )
                ).scalar_one()
            )

        scheduler.start()
        jobs = scheduler.get_jobs()
        next_runs = ", ".join(
            f"{j.id}={j.next_run_time.strftime('%Y-%m-%d %H:%M%z') if j.next_run_time else 'none'}"
            for j in jobs[:4]
        )
        logger.info(
            "Worker boot ready | env=%s | timezone=%s | vn_now=%s | active_sources=%d | "
            "scheduled_jobs=%d (%s...) | retention_days=%d | poll_interval=%ds | status=PRODUCTION_READY",
            settings.app_env,
            VN_TZ_NAME,
            vn_now().isoformat(),
            active_sources,
            len(jobs),
            next_runs,
            settings.snapshot_retention_days,
            settings.worker_poll_interval_seconds,
        )

        while not stop_event.is_set():
            try:
                summary = await runner.poll_once()
                if summary is not None:
                    logger.info(
                        "Executed queued run %s | status=%s | ok=%d/%d | verified=%d | flagged=%d",
                        summary.run_code,
                        summary.status,
                        summary.pages_fetched_success,
                        summary.sources_queried_count,
                        summary.records_verified_count,
                        summary.records_flagged_count,
                    )
                f_summary = await poll_queued_forecast_run(engine, session_factory)
                if f_summary is not None:
                    logger.info(
                        "Executed queued forecast run | scope=%s | ready=%d | insufficient=%d | eval=%d",
                        f_summary.scope,
                        f_summary.ready_count,
                        f_summary.insufficient_count,
                        f_summary.evaluations_inserted,
                    )
            except Exception:
                logger.exception("Unhandled error during queue poll iteration")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=float(settings.worker_poll_interval_seconds),
                )
    finally:
        with contextlib.suppress(Exception):
            scheduler.shutdown(wait=False)
        await engine.dispose()
        logger.info("Worker shut down cleanly.")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_worker_daemon())


if __name__ == "__main__":
    main()
