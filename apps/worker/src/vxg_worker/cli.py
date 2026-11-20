"""CLI entrypoint to run or enqueue manual/scheduled crawl jobs immediately."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date, timedelta

from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.enums import CrawlScope, ForecastStatus, TriggerType
from vxg_shared.timezone import vn_today
from vxg_worker.config import WorkerSettings
from vxg_worker.forecasting.evaluator import evaluate_due_forecasts
from vxg_worker.forecasting.pipeline import run_forecast_pipeline
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

    run_fc_p = subparsers.add_parser(
        "run-forecasts",
        help="Run zero look-ahead econometric forecasts across Gold/Fuel/Gas",
    )
    run_fc_p.add_argument(
        "--scope",
        choices=[s.value for s in CrawlScope],
        default=CrawlScope.ALL.value,
    )
    run_fc_p.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Optional cutoff date YYYY-MM-DD (default: today Asia/Ho_Chi_Minh)",
    )
    run_fc_p.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional existing QUEUED ForecastRun UUID to execute in place",
    )
    run_fc_p.add_argument(
        "--with-backtest",
        action="store_true",
        help="Also persist walk-forward BACKTEST rows into forecast_evaluations",
    )

    eval_fc_p = subparsers.add_parser(
        "evaluate-forecasts",
        help="Evaluate due LIVE forecast snapshots against realized daily_price_series",
    )
    eval_fc_p.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Optional evaluation cutoff date YYYY-MM-DD (default: today Asia/Ho_Chi_Minh)",
    )

    bt_fc_p = subparsers.add_parser(
        "backtest-forecasts",
        help="Run expanding-window walk-forward backtests, persist BACKTEST rows, and print summary",
    )
    bt_fc_p.add_argument(
        "--scope",
        choices=[s.value for s in CrawlScope],
        default=CrawlScope.ALL.value,
    )
    bt_fc_p.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Optional evaluation cutoff date YYYY-MM-DD (default: today Asia/Ho_Chi_Minh)",
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

        if args.command == "evaluate-forecasts":
            as_of_val = date.fromisoformat(args.as_of.strip()) if args.as_of and args.as_of.strip() else None
            async with session_factory() as session:
                eval_sum = await evaluate_due_forecasts(session, as_of_date=as_of_val)
                await session.commit()
            print(
                f"Forecast evaluation completed: as_of={eval_sum.as_of_date.isoformat()} "
                f"due={eval_sum.due_candidates} inserted={eval_sum.evaluated_inserted} "
                f"waiting_actual={eval_sum.waiting_actual_price}"
            )
            return 0

        if args.command in ("run-forecasts", "backtest-forecasts"):
            as_of_val = date.fromisoformat(args.as_of.strip()) if args.as_of and args.as_of.strip() else None
            is_backtest_cmd = args.command == "backtest-forecasts"
            fc_run_id = (
                uuid.UUID(args.run_id.strip())
                if not is_backtest_cmd and getattr(args, "run_id", None) and str(args.run_id).strip()
                else None
            )
            with_bt = is_backtest_cmd or bool(getattr(args, "with_backtest", False))
            fc_sum = await run_forecast_pipeline(
                engine,
                session_factory,
                scope=CrawlScope(args.scope),
                as_of_date=as_of_val,
                existing_run_id=fc_run_id,
                persist_backtest=with_bt,
                run_evaluation_first=(args.command == "run-forecasts"),
            )
            print(
                f"\nForecast pipeline: cmd={args.command} scope={fc_sum.scope} as_of={fc_sum.as_of_date.isoformat()} "
                f"total={fc_sum.total_snapshots} ready={fc_sum.ready_count} "
                f"insufficient={fc_sum.insufficient_count} eval_live={fc_sum.evaluations_inserted} "
                f"backtest_rows={fc_sum.backtest_rows_upserted} duration_ms={fc_sum.duration_ms}"
            )
            print("-" * 124)
            print(
                f"{'COMMODITY':<22} {'LOC':<7} {'HORIZON':<16} {'MODEL':<24} "
                f"{'POINT_VND':>12} {'DIR':<5} {'CONF':>5} {'MAE_VND':>10} {'MAPE%':>7} {'NAIVE%':>7} {'DIR%':>6}"
            )
            print("-" * 124)
            for out in fc_sum.outputs:
                if out.status != ForecastStatus.READY:
                    continue
                bt = out.drivers_json.get("backtest") or {}
                mae_s = f"{bt.get('mae_vnd'):,}" if bt.get("mae_vnd") is not None else "-"
                mape_s = f"{bt.get('mape_pct'):.2f}" if bt.get("mape_pct") is not None else "-"
                n_mape_s = f"{bt.get('naive_mape_pct'):.2f}" if bt.get("naive_mape_pct") is not None else "-"
                dir_s = f"{bt.get('directional_accuracy_pct'):.1f}" if bt.get("directional_accuracy_pct") is not None else "-"
                pt_s = f"{out.point_estimate_vnd:,}" if out.point_estimate_vnd is not None else "-"
                conf_s = f"{float(out.confidence_score):.2f}" if out.confidence_score is not None else "-"
                print(
                    f"{out.commodity_code:<22} {out.location_code:<7} {out.horizon.value:<16} "
                    f"{out.model_version:<24} {pt_s:>12} {out.predicted_direction.value:<5} "
                    f"{conf_s:>5} {mae_s:>10} {mape_s:>7} {n_mape_s:>7} {dir_s:>6}"
                )
            print("-" * 124 + "\n")
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
