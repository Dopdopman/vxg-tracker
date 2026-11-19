"""CLI entrypoint to run or enqueue manual/scheduled crawl jobs immediately."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.enums import CrawlScope, TriggerType
from vxg_worker.config import WorkerSettings
from vxg_worker.runner import CrawlRunner, CrawlRunSummary, enqueue_crawl_run
from vxg_worker.storage.snapshots import prune_expired_snapshots


def _print_summary(summary: CrawlRunSummary) -> None:
    print(
        f"\nCrawlRun: {summary.run_code} | status={summary.status} | "
        f"scope={summary.scope} | trigger={summary.trigger_type} | duration_ms={summary.duration_ms}"
    )
    print(
        f"Counters: queried={summary.sources_queried_count} | "
        f"fetched_ok={summary.pages_fetched_success} | "
        f"fetched_failed={summary.pages_fetched_failed} | "
        f"docs_parsed={summary.documents_parsed_count} | "
        f"extracted={summary.records_extracted_count} | "
        f"verified={summary.records_verified_count} | "
        f"flagged={summary.records_flagged_count} | "
        f"unchanged={summary.records_unchanged_count}"
    )
    print("-" * 124)
    print(
        f"{'SOURCE':<26} {'TIER':<11} {'STATUS':<13} {'HTTP':<5} "
        f"{'MS':>7} {'BYTES':>9} {'SHA256':<14} {'DETAIL'}"
    )
    print("-" * 124)
    for item in summary.source_logs:
        tier_label = f"{item.tier_used}{'*' if item.fallback_triggered else ''}"
        http_str = str(item.http_status) if item.http_status is not None else "-"
        sha_short = item.content_sha256[:12] if item.content_sha256 else "-"
        detail = item.error_message or "OK"
        print(
            f"{item.source_code:<26} {tier_label:<11} {item.fetch_status:<13} "
            f"{http_str:<5} {item.response_time_ms:>7} {item.byte_size:>9} "
            f"{sha_short:<14} {detail[:42]}"
        )
    print("-" * 124)
    print("(* = fallback from Tier 1 HTTPX to Tier 2 PLAYWRIGHT)\n")


async def _async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vxg-worker-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_p = subparsers.add_parser("run", help="Execute a crawl run immediately")
    run_p.add_argument(
        "--scope",
        choices=[s.value for s in CrawlScope],
        default=CrawlScope.ALL.value,
    )
    run_p.add_argument(
        "--trigger",
        choices=[t.value for t in TriggerType],
        default=TriggerType.MANUAL_ADMIN.value,
    )
    run_p.add_argument(
        "--smoke-only",
        action="store_true",
        help="Verify live fetch + snapshot storage + audit telemetry without price parsing",
    )
    run_p.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional existing QUEUED CrawlRun UUID to execute in place",
    )

    enq_p = subparsers.add_parser("enqueue", help="Insert a QUEUED crawl run for worker daemon")
    enq_p.add_argument(
        "--scope",
        choices=[s.value for s in CrawlScope],
        default=CrawlScope.ALL.value,
    )
    enq_p.add_argument(
        "--trigger",
        choices=[t.value for t in TriggerType],
        default=TriggerType.MANUAL_ADMIN.value,
    )

    prune_p = subparsers.add_parser(
        "prune-snapshots",
        help="Delete expired raw_evidence_snapshots beyond retention window while keeping history",
    )
    prune_p.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Override SNAPSHOT_RETENTION_DAYS (default 90)",
    )

    args = parser.parse_args(argv)
    settings = WorkerSettings()
    engine = create_async_db_engine(settings.async_database_url)
    session_factory = create_session_factory(engine)

    try:
        if args.command == "prune-snapshots":
            days = args.retention_days or settings.snapshot_retention_days
            async with session_factory() as session:
                prune_res = await prune_expired_snapshots(session, retention_days=days)
                await session.commit()
            print(
                f"Pruned expired snapshots: retention_days={prune_res.retention_days} "
                f"cutoff={prune_res.cutoff_vn.isoformat()} "
                f"deleted={prune_res.deleted_count} freed_bytes={prune_res.freed_bytes}"
            )
            return 0

        if args.command == "enqueue":
            run = await enqueue_crawl_run(
                session_factory,
                scope=CrawlScope(args.scope),
                trigger_type=TriggerType(args.trigger),
            )
            print(f"Enqueued QUEUED CrawlRun: id={run.id} run_code={run.run_code}")
            return 0

        parsed_run_id = (
            uuid.UUID(args.run_id.strip())
            if args.run_id and args.run_id.strip()
            else None
        )
        runner = CrawlRunner(
            engine=engine,
            session_factory=session_factory,
            settings=settings,
        )
        summary = await runner.execute_run(
            scope=CrawlScope(args.scope),
            trigger_type=TriggerType(args.trigger),
            existing_run_id=parsed_run_id,
            smoke_only=bool(args.smoke_only),
        )
        _print_summary(summary)
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    sys.exit(main())
