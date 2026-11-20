"""CLI entrypoint to run or enqueue manual/scheduled crawl jobs immediately."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import timedelta

from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.enums import CrawlScope, TriggerType
from vxg_shared.timezone import vn_today
from vxg_worker.config import WorkerSettings
from vxg_worker.history.orchestrator import BackfillDomain, run_history_backfill
from vxg_worker.history.rollup import rollup_daily_from_price_records
from vxg_worker.macro.sync import run_macro_sync
from vxg_worker.news.sync import run_news_sync
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

    rollup_p = subparsers.add_parser(
        "rollup-daily",
        help="Promote clean VERIFIED/CROSS_CHECKED Phase 1 prices into daily_price_series",
    )
    rollup_p.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Only roll up records with effective_date within the last N days (default: all)",
    )

    backfill_p = subparsers.add_parser(
        "backfill-history",
        help="Backfill 12-24 months of real historical Gold/Fuel/Gas series into daily_price_series",
    )
    backfill_p.add_argument(
        "--domain",
        choices=[d.value for d in BackfillDomain],
        default=BackfillDomain.ALL.value,
    )
    backfill_p.add_argument(
        "--months",
        type=int,
        default=24,
        help="History window in calendar months (default: 24)",
    )

    sync_macro_p = subparsers.add_parser(
        "sync-macro",
        help="Sync recent daily macroeconomic indicators (Yahoo Chart v8 + Vietcombank USD/VND)",
    )
    sync_macro_p.add_argument(
        "--days",
        type=int,
        default=14,
        help="Recent calendar days window for Vietcombank FX & short macro refresh (default: 14)",
    )

    backfill_macro_p = subparsers.add_parser(
        "backfill-macro",
        help="Backfill 12-24+ months of Yahoo macro series + bounded recent Vietcombank FX window",
    )
    backfill_macro_p.add_argument(
        "--months",
        type=int,
        default=24,
        help="Macro history window in months (default: 24)",
    )
    backfill_macro_p.add_argument(
        "--vcb-days",
        type=int,
        default=45,
        help="Recent Vietcombank FX backfill window in days (capped at 60, default: 45)",
    )

    sync_news_p = subparsers.add_parser(
        "sync-news",
        help="Fetch RSS financial feeds, score deterministically, and optionally summarize via Gemini",
    )
    sync_news_p.add_argument(
        "--hours",
        type=int,
        default=72,
        help="Published lookback window in hours (default: 72)",
    )
    sync_news_p.add_argument(
        "--no-gemini",
        action="store_true",
        help="Force Tier 1 deterministic lexicon scoring only (skip Gemini even if API key is set)",
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

        if args.command == "rollup-daily":
            since_date = (
                vn_today() - timedelta(days=int(args.since_days))
                if args.since_days is not None
                else None
            )
            async with session_factory() as session:
                rolled = await rollup_daily_from_price_records(session, since_date=since_date)
                await session.commit()
            print(f"Rolled up daily_price_series rows: count={rolled} since_date={since_date}")
            return 0

        if args.command == "backfill-history":
            summary_bf = await run_history_backfill(
                engine,
                session_factory,
                domain=BackfillDomain(args.domain),
                months=max(1, int(args.months)),
            )
            print(
                f"History backfill completed: domain={summary_bf.domain} "
                f"months={summary_bf.months} duration_ms={summary_bf.duration_ms} "
                f"stats={summary_bf.domain_stats} rollup_rows={summary_bf.rollup_rows}"
            )
            return 0

        if args.command == "sync-macro":
            summary_macro = await run_macro_sync(
                engine,
                session_factory,
                mode="sync",
                sync_days=max(1, min(int(args.days), 60)),
            )
            print(
                f"Macro sync completed: mode={summary_macro.mode} "
                f"duration_ms={summary_macro.duration_ms} "
                f"upserted={summary_macro.upserted_by_indicator} "
                f"errors={summary_macro.errors}"
            )
            return 0

        if args.command == "backfill-macro":
            summary_macro_bf = await run_macro_sync(
                engine,
                session_factory,
                mode="backfill",
                months=max(1, int(args.months)),
                vcb_days=max(1, min(int(args.vcb_days), 60)),
            )
            print(
                f"Macro backfill completed: mode={summary_macro_bf.mode} "
                f"months={args.months} duration_ms={summary_macro_bf.duration_ms} "
                f"upserted={summary_macro_bf.upserted_by_indicator} "
                f"errors={summary_macro_bf.errors}"
            )
            return 0

        if args.command == "sync-news":
            summary_news = await run_news_sync(
                engine,
                session_factory,
                settings=settings,
                hours=max(1, int(args.hours)),
                enable_gemini=not bool(args.no_gemini),
            )
            print(
                f"News sync completed: hours={summary_news.hours} "
                f"duration_ms={summary_news.duration_ms} feeds_ok={summary_news.feeds_ok} "
                f"feeds_failed={summary_news.feeds_failed} raw_items={summary_news.raw_items} "
                f"relevant={summary_news.relevant_items} inserted={summary_news.inserted_count} "
                f"by_domain={summary_news.by_domain} gemini={summary_news.gemini_stats} "
                f"errors={summary_news.errors}"
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
