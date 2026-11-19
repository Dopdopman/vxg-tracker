"""CrawlRun orchestrator: PG Advisory Lock, 2-Tier & 2-Step Fetch, Cross-Check, & Audit Telemetry."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vxg_shared.db.models import AnomalyAlert, CrawlRun, CrawlSourceLog, Source
from vxg_shared.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    CommodityCategory,
    CrawlRunStatus,
    CrawlScope,
    FetchStatus,
    PriceUnit,
    PricingType,
    SourceTier,
    TriggerType,
)
from vxg_shared.timezone import format_vn_run_code, vn_now
from vxg_worker.adapters.base import AdapterParseResult, AdapterRegistry, SchemaDriftError
from vxg_worker.adapters.registry import build_default_adapter_registry
from vxg_worker.config import WorkerSettings
from vxg_worker.fetcher.two_tier import TwoTierFetcher
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.locks import CrawlLockBusyError, PostgresAdvisoryLock
from vxg_worker.notifier import AlertNotifier
from vxg_worker.persistence import (
    get_latest_verified_price,
    load_catalog_lookup,
    persist_validated_candidate,
)
from vxg_worker.storage.snapshots import store_raw_snapshot
from vxg_worker.validation.guardrails import PriceCandidate, ValidationContext, ValidationEngine

logger = logging.getLogger("vxg_worker.runner")


@dataclass(slots=True)
class SourceLogSummary:
    """In-memory diagnostic summary of one queried source in a CrawlRun."""

    source_code: str
    request_url: str
    tier_used: str
    fetch_status: str
    http_status: int | None
    response_time_ms: int
    byte_size: int
    content_sha256: str | None
    fallback_triggered: bool
    documents_parsed: int
    records_extracted: int
    records_verified: int
    records_flagged: int
    error_message: str | None


@dataclass(slots=True)
class CrawlRunSummary:
    """Audit summary returned after completing a CrawlRun."""

    run_id: uuid.UUID
    run_code: str
    scope: str
    trigger_type: str
    status: str
    sources_queried_count: int
    pages_fetched_success: int
    pages_fetched_failed: int
    documents_parsed_count: int
    records_extracted_count: int
    records_verified_count: int
    records_flagged_count: int
    records_unchanged_count: int
    duration_ms: int
    source_logs: list[SourceLogSummary] = field(default_factory=list)


@dataclass(slots=True)
class _ParsedSourceBatch:
    source: Source
    source_log: CrawlSourceLog
    fetch_result: FetchResult
    parse_result: AdapterParseResult
    metadata: dict[str, object]
    summary_idx: int


def _compute_candidate_delta_pct(primary: PriceCandidate, reference: PriceCandidate) -> float:
    deltas: list[float] = []
    for p_val, r_val in (
        (primary.buy_price_vnd, reference.buy_price_vnd),
        (primary.sell_price_vnd, reference.sell_price_vnd),
        (primary.retail_price_vnd, reference.retail_price_vnd),
    ):
        if isinstance(p_val, int) and isinstance(r_val, int) and r_val > 0:
            deltas.append(abs(p_val - r_val) / r_val * 100.0)
    return max(deltas) if deltas else 0.0


async def enqueue_crawl_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    scope: CrawlScope = CrawlScope.ALL,
    trigger_type: TriggerType = TriggerType.MANUAL_ADMIN,
) -> CrawlRun:
    """Insert a QUEUED CrawlRun row for asynchronous execution by the worker daemon."""
    async with session_factory() as session:
        run = CrawlRun(
            run_code=format_vn_run_code(scope.value),
            scope_category=scope.value,
            trigger_type=trigger_type.value,
            status=CrawlRunStatus.QUEUED.value,
            started_at=vn_now(),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


class CrawlRunner:
    """Executes scheduled, manual, or queued crawl runs under a PostgreSQL Advisory Lock."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        settings: WorkerSettings | None = None,
        adapter_registry: AdapterRegistry | None = None,
        fetcher: TwoTierFetcher | None = None,
        validator: ValidationEngine | None = None,
        notifier: AlertNotifier | None = None,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._settings = settings or WorkerSettings()
        self._registry = adapter_registry or build_default_adapter_registry()
        self._injected_fetcher = fetcher
        self._validator = validator or ValidationEngine(
            spike_threshold_pct=self._settings.price_spike_threshold_pct,
            gold_max_spread_pct=self._settings.gold_max_spread_pct,
        )
        self._notifier = notifier or AlertNotifier.from_settings(self._settings)

    async def poll_once(self, *, smoke_only: bool = False) -> CrawlRunSummary | None:
        """Check for the oldest QUEUED CrawlRun and execute it under advisory lock."""
        async with self._session_factory() as session:
            queued_id = (
                await session.execute(
                    select(CrawlRun.id)
                    .where(CrawlRun.status == CrawlRunStatus.QUEUED.value)
                    .order_by(CrawlRun.started_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if queued_id is None:
            return None

        try:
            return await self.execute_run(
                existing_run_id=queued_id,
                smoke_only=smoke_only,
            )
        except CrawlLockBusyError:
            logger.warning("Advisory lock busy; deferring QUEUED run %s", queued_id)
            return None

    async def execute_run(
        self,
        *,
        scope: CrawlScope = CrawlScope.ALL,
        trigger_type: TriggerType = TriggerType.MANUAL_ADMIN,
        existing_run_id: uuid.UUID | None = None,
        smoke_only: bool = False,
    ) -> CrawlRunSummary:
        """Run a full crawl + validation + audit telemetry cycle inside PG Advisory Lock."""
        async with PostgresAdvisoryLock(
            self._engine, self._settings.advisory_lock_key
        ) as acquired:
            if not acquired:
                raise CrawlLockBusyError(
                    f"Advisory lock {self._settings.advisory_lock_key} is already held"
                )

            run_mono_start = time.monotonic()
            async with self._session_factory() as session:
                if existing_run_id is not None:
                    run = await session.get(CrawlRun, existing_run_id)
                    if run is None:
                        raise ValueError(f"CrawlRun {existing_run_id} not found")
                    if run.status != CrawlRunStatus.QUEUED.value:
                        raise ValueError(
                            f"CrawlRun {existing_run_id} is not QUEUED (status={run.status})"
                        )
                    run.status = CrawlRunStatus.RUNNING.value
                    run.started_at = vn_now()
                    scope = CrawlScope(run.scope_category)
                    trigger_type = TriggerType(run.trigger_type)
                else:
                    run = CrawlRun(
                        run_code=format_vn_run_code(scope.value),
                        scope_category=scope.value,
                        trigger_type=trigger_type.value,
                        status=CrawlRunStatus.RUNNING.value,
                        started_at=vn_now(),
                    )
                    session.add(run)
                await session.commit()
                run_id = run.id
                run_code = run.run_code

            try:
                if self._injected_fetcher is not None:
                    return await self._execute_with_fetcher(
                        fetcher=self._injected_fetcher,
                        run_id=run_id,
                        run_code=run_code,
                        scope=scope,
                        trigger_type=trigger_type,
                        run_mono_start=run_mono_start,
                        smoke_only=smoke_only,
                    )

                async with TwoTierFetcher(
                    proxy_url=self._settings.proxy_url,
                    playwright_timeout_ms=self._settings.default_playwright_timeout_ms,
                ) as owned_fetcher:
                    return await self._execute_with_fetcher(
                        fetcher=owned_fetcher,
                        run_id=run_id,
                        run_code=run_code,
                        scope=scope,
                        trigger_type=trigger_type,
                        run_mono_start=run_mono_start,
                        smoke_only=smoke_only,
                    )
            except Exception as exc:
                duration_ms = int((time.monotonic() - run_mono_start) * 1000)
                async with self._session_factory() as fail_session:
                    run_row = await fail_session.get(CrawlRun, run_id)
                    if run_row is not None:
                        run_row.status = CrawlRunStatus.FAILED.value
                        run_row.finished_at = vn_now()
                        run_row.duration_ms = duration_ms
                        run_row.error_summary = str(exc)[:2000]
                        await fail_session.commit()
                raise

    async def _execute_with_fetcher(
        self,
        *,
        fetcher: TwoTierFetcher,
        run_id: uuid.UUID,
        run_code: str,
        scope: CrawlScope,
        trigger_type: TriggerType,
        run_mono_start: float,
        smoke_only: bool,
    ) -> CrawlRunSummary:
        source_summaries: list[SourceLogSummary] = []
        parsed_batches: list[_ParsedSourceBatch] = []
        tier2_refs: dict[tuple[str, str], tuple[int, PriceCandidate]] = {}

        sources_queried = 0
        pages_success = 0
        pages_failed = 0
        docs_parsed = 0
        rec_extracted = 0
        rec_verified = 0
        rec_flagged = 0
        rec_unchanged = 0
        drift_or_parse_errors = 0
        run_errors: list[str] = []
        raised_anomalies: list[str] = []

        async with self._session_factory() as session:
            stmt = select(Source).where(Source.is_active.is_(True)).order_by(Source.id.asc())
            if scope != CrawlScope.ALL:
                stmt = stmt.where(Source.category == scope.value)
            sources = list((await session.execute(stmt)).scalars().all())
            catalog = await load_catalog_lookup(session)

            # --- Phase 1: Fetch (including 2-step listing->detail) & Parse ---
            for source in sources:
                sources_queried += 1
                fetch_result = await fetcher.fetch_source(source)
                active_result = fetch_result
                total_elapsed_ms = fetch_result.response_time_ms

                snapshot_id: uuid.UUID | None = None
                content_sha256 = fetch_result.content_sha256
                if fetch_result.raw_bytes:
                    snapshot_id, content_sha256 = await store_raw_snapshot(
                        session,
                        raw_bytes=fetch_result.raw_bytes,
                        content_type=fetch_result.content_type,
                    )

                final_fetch_status = fetch_result.fetch_status
                error_code = fetch_result.error_code
                error_message = fetch_result.error_message
                base_metadata: dict[str, object] = {
                    "fallback_triggered": fetch_result.fallback_triggered,
                    "attempts": fetch_result.attempts,
                    **fetch_result.metadata,
                }

                source_log = CrawlSourceLog(
                    crawl_run_id=run_id,
                    source_id=source.id,
                    snapshot_id=snapshot_id,
                    request_url=fetch_result.request_url,
                    final_url=fetch_result.final_url,
                    http_status=fetch_result.http_status,
                    fetch_strategy_used=fetch_result.tier_used.value,
                    response_time_ms=total_elapsed_ms,
                    attempt_number=fetch_result.attempts,
                    fetch_status=final_fetch_status.value,
                    documents_parsed=0,
                    records_extracted=0,
                    records_verified=0,
                    records_flagged=0,
                    content_sha256=content_sha256,
                    error_code=error_code,
                    error_message=error_message,
                    parser_metadata=dict(base_metadata),
                )
                session.add(source_log)
                await session.flush()

                summary_idx = len(source_summaries)
                src_docs = 0

                if fetch_result.fetch_status == FetchStatus.SUCCESS:
                    try:
                        adapter = self._registry.resolve(source.parser_key, smoke_only=smoke_only)
                        detail_url = (
                            None
                            if smoke_only
                            else adapter.extract_detail_url(fetch_result, source)
                        )
                        if detail_url:
                            detail_result = await fetcher.fetch_url(
                                detail_url,
                                fetch_strategy=source.fetch_strategy,
                                timeout_ms=source.timeout_ms,
                                rate_limit_delay_ms=source.rate_limit_delay_ms,
                                referer=fetch_result.final_url or source.endpoint_url,
                            )
                            total_elapsed_ms += detail_result.response_time_ms
                            base_metadata["listing_url"] = source.endpoint_url
                            base_metadata["detail_url"] = detail_url
                            active_result = detail_result
                            if detail_result.raw_bytes:
                                snapshot_id, content_sha256 = await store_raw_snapshot(
                                    session,
                                    raw_bytes=detail_result.raw_bytes,
                                    content_type=detail_result.content_type,
                                )
                            source_log.snapshot_id = snapshot_id
                            source_log.content_sha256 = content_sha256
                            source_log.final_url = detail_result.final_url or detail_url
                            source_log.http_status = detail_result.http_status
                            source_log.fetch_strategy_used = detail_result.tier_used.value
                            source_log.response_time_ms = total_elapsed_ms
                            if detail_result.fetch_status != FetchStatus.SUCCESS:
                                final_fetch_status = detail_result.fetch_status
                                error_code = detail_result.error_code
                                error_message = detail_result.error_message
                                pages_failed += 1
                                run_errors.append(
                                    f"{source.code}: detail {detail_result.fetch_status.value} "
                                    f"({error_message or error_code})"
                                )
                                raised_anomalies.extend(
                                    await self._record_source_failure(
                                        session=session,
                                        source=source,
                                        run_id=run_id,
                                        source_log_id=source_log.id,
                                        alert_type=None,
                                        message=error_message or "Detail page fetch failed",
                                    )
                                )
                                source_log.fetch_status = final_fetch_status.value
                                source_log.error_code = error_code
                                source_log.error_message = error_message
                                source_log.parser_metadata = dict(base_metadata)
                                source_summaries.append(
                                    SourceLogSummary(
                                        source_code=source.code,
                                        request_url=source.endpoint_url,
                                        tier_used=active_result.tier_used.value,
                                        fetch_status=final_fetch_status.value,
                                        http_status=active_result.http_status,
                                        response_time_ms=total_elapsed_ms,
                                        byte_size=active_result.byte_size,
                                        content_sha256=content_sha256,
                                        fallback_triggered=(
                                            fetch_result.fallback_triggered
                                            or detail_result.fallback_triggered
                                        ),
                                        documents_parsed=0,
                                        records_extracted=0,
                                        records_verified=0,
                                        records_flagged=0,
                                        error_message=error_message,
                                    )
                                )
                                continue

                        pages_success += 1
                        parse_res = adapter.parse(active_result, source)
                        src_docs = parse_res.documents_parsed
                        docs_parsed += src_docs
                        base_metadata = {**base_metadata, **parse_res.parser_metadata}

                        if source.tier == SourceTier.SECONDARY_CROSSCHECK.value:
                            for cand in parse_res.candidates:
                                key = (cand.commodity_code, cand.location_code)
                                tier2_refs.setdefault(key, (source.id, cand))

                        source.last_success_at = vn_now()
                        source.consecutive_failures = 0
                        parsed_batches.append(
                            _ParsedSourceBatch(
                                source=source,
                                source_log=source_log,
                                fetch_result=active_result,
                                parse_result=parse_res,
                                metadata=base_metadata,
                                summary_idx=summary_idx,
                            )
                        )
                    except SchemaDriftError as exc:
                        pages_success += 1
                        drift_or_parse_errors += 1
                        final_fetch_status = FetchStatus.SCHEMA_DRIFT
                        error_code = "SCHEMA_DRIFT"
                        error_message = str(exc)
                        run_errors.append(f"{source.code}: {error_message}")
                        raised_anomalies.extend(
                            await self._record_source_failure(
                                session=session,
                                source=source,
                                run_id=run_id,
                                source_log_id=source_log.id,
                                alert_type=AlertType.SCHEMA_DRIFT,
                                message=error_message,
                            )
                        )
                    except Exception as exc:
                        pages_success += 1
                        drift_or_parse_errors += 1
                        final_fetch_status = FetchStatus.PARSE_ERROR
                        error_code = type(exc).__name__
                        error_message = str(exc)
                        run_errors.append(f"{source.code}: {error_message}")
                        raised_anomalies.extend(
                            await self._record_source_failure(
                                session=session,
                                source=source,
                                run_id=run_id,
                                source_log_id=source_log.id,
                                alert_type=AlertType.VALIDATION_ERROR,
                                message=error_message,
                            )
                        )
                else:
                    pages_failed += 1
                    run_errors.append(
                        f"{source.code}: {fetch_result.fetch_status.value} "
                        f"({error_message or error_code})"
                    )
                    raised_anomalies.extend(
                        await self._record_source_failure(
                            session=session,
                            source=source,
                            run_id=run_id,
                            source_log_id=source_log.id,
                            alert_type=None,
                            message=error_message or "Fetch failed",
                        )
                    )

                source_log.fetch_status = final_fetch_status.value
                source_log.documents_parsed = src_docs
                source_log.error_code = error_code
                source_log.error_message = error_message
                source_log.parser_metadata = dict(base_metadata)

                source_summaries.append(
                    SourceLogSummary(
                        source_code=source.code,
                        request_url=source.endpoint_url,
                        tier_used=active_result.tier_used.value,
                        fetch_status=final_fetch_status.value,
                        http_status=active_result.http_status,
                        response_time_ms=total_elapsed_ms,
                        byte_size=active_result.byte_size,
                        content_sha256=content_sha256,
                        fallback_triggered=active_result.fallback_triggered,
                        documents_parsed=src_docs,
                        records_extracted=0,
                        records_verified=0,
                        records_flagged=0,
                        error_message=error_message,
                    )
                )

            # --- Phase 2: Tier 1 <-> Tier 2 Cross-Check, 6-Layer Validation, & Persistence ---
            for batch in parsed_batches:
                src = batch.source
                src_log = batch.source_log
                summary_item = source_summaries[batch.summary_idx]
                src_extracted = len(batch.parse_result.candidates)
                src_verified = 0
                src_flagged = 0
                rec_extracted += src_extracted

                if src.tier == SourceTier.SECONDARY_CROSSCHECK.value:
                    batch.metadata["cross_check_reference_items"] = src_extracted
                    src_log.records_extracted = src_extracted
                    src_log.parser_metadata = dict(batch.metadata)
                    summary_item.records_extracted = src_extracted
                    continue

                for candidate in batch.parse_result.candidates:
                    commodity = catalog.commodities_by_code.get(candidate.commodity_code)
                    location = catalog.locations_by_code.get(candidate.location_code)
                    if commodity is None or location is None:
                        unknown_msg = (
                            f"Unknown catalog mapping: commodity={candidate.commodity_code}, "
                            f"location={candidate.location_code}"
                        )
                        session.add(
                            AnomalyAlert(
                                crawl_run_id=run_id,
                                source_log_id=src_log.id,
                                alert_type=AlertType.VALIDATION_ERROR.value,
                                severity=AlertSeverity.MEDIUM.value,
                                status=AlertStatus.OPEN.value,
                                message=unknown_msg,
                            )
                        )
                        raised_anomalies.append(unknown_msg)
                        continue

                    t2_match = tier2_refs.get((candidate.commodity_code, candidate.location_code))
                    if t2_match is not None and candidate.cross_check_source_id is None:
                        t2_source_id, t2_cand = t2_match
                        delta_pct = _compute_candidate_delta_pct(candidate, t2_cand)
                        candidate.cross_check_source_id = t2_source_id
                        candidate.cross_check_delta_pct = Decimal(f"{delta_pct:.3f}")

                    prev_verified = await get_latest_verified_price(
                        session,
                        commodity_id=commodity.id,
                        location_id=location.id,
                    )
                    ctx = ValidationContext(
                        commodity_id=commodity.id,
                        commodity_code=commodity.code,
                        category=CommodityCategory(commodity.category),
                        unit=PriceUnit(commodity.unit),
                        pricing_type=PricingType(commodity.pricing_type),
                        spec_detail=commodity.spec_detail,
                        location_id=location.id,
                        source_id=src.id,
                        previous_verified=prev_verified,
                    )
                    decision = self._validator.validate(candidate, ctx)
                    outcome = await persist_validated_candidate(
                        session,
                        candidate=candidate,
                        decision=decision,
                        commodity_id=commodity.id,
                        location_id=location.id,
                        source_id=src.id,
                        crawl_run_id=run_id,
                        source_log_id=src_log.id,
                    )
                    if outcome == "VERIFIED":
                        src_verified += 1
                        rec_verified += 1
                    elif outcome == "UNCHANGED":
                        rec_unchanged += 1
                    elif outcome == "FLAGGED":
                        src_flagged += 1
                        rec_flagged += 1
                        raised_anomalies.append(
                            f"{candidate.commodity_code}/{candidate.location_code}: "
                            f"{decision.failure_reason or 'Flagged by guardrails'}"
                        )

                src_log.records_extracted = src_extracted
                src_log.records_verified = src_verified
                src_log.records_flagged = src_flagged
                src_log.parser_metadata = dict(batch.metadata)
                summary_item.records_extracted = src_extracted
                summary_item.records_verified = src_verified
                summary_item.records_flagged = src_flagged

            run_status = self._determine_run_status(
                sources_queried=sources_queried,
                pages_success=pages_success,
                pages_failed=pages_failed,
                drift_or_parse_errors=drift_or_parse_errors,
            )
            duration_ms = int((time.monotonic() - run_mono_start) * 1000)
            run_row = await session.get(CrawlRun, run_id)
            assert run_row is not None
            run_row.status = run_status.value
            run_row.sources_queried_count = sources_queried
            run_row.pages_fetched_success = pages_success
            run_row.pages_fetched_failed = pages_failed
            run_row.documents_parsed_count = docs_parsed
            run_row.records_extracted_count = rec_extracted
            run_row.records_verified_count = rec_verified
            run_row.records_flagged_count = rec_flagged
            run_row.records_unchanged_count = rec_unchanged
            run_row.finished_at = vn_now()
            run_row.duration_ms = duration_ms
            run_row.error_summary = "; ".join(run_errors)[:2000] if run_errors else None

            await session.commit()

        summary = CrawlRunSummary(
            run_id=run_id,
            run_code=run_code,
            scope=scope.value,
            trigger_type=trigger_type.value,
            status=run_status.value,
            sources_queried_count=sources_queried,
            pages_fetched_success=pages_success,
            pages_fetched_failed=pages_failed,
            documents_parsed_count=docs_parsed,
            records_extracted_count=rec_extracted,
            records_verified_count=rec_verified,
            records_flagged_count=rec_flagged,
            records_unchanged_count=rec_unchanged,
            duration_ms=duration_ms,
            source_logs=source_summaries,
        )
        try:
            await self._notifier.notify_crawl_run(summary, anomaly_messages=raised_anomalies)
        except Exception:
            logger.exception("Unexpected error delivering alert for run %s", run_code)
        return summary

    async def _record_source_failure(
        self,
        *,
        session: AsyncSession,
        source: Source,
        run_id: uuid.UUID,
        source_log_id: uuid.UUID,
        alert_type: AlertType | None,
        message: str,
    ) -> list[str]:
        raised: list[str] = []
        source.consecutive_failures += 1
        if alert_type is not None:
            alert_msg = f"{source.code}: {message}"
            session.add(
                AnomalyAlert(
                    crawl_run_id=run_id,
                    source_log_id=source_log_id,
                    alert_type=alert_type.value,
                    severity=AlertSeverity.HIGH.value,
                    status=AlertStatus.OPEN.value,
                    message=alert_msg,
                )
            )
            raised.append(alert_msg)
        if source.consecutive_failures >= 3:
            down_msg = (
                f"Source {source.code} failed {source.consecutive_failures} consecutive times: "
                f"{message}"
            )
            session.add(
                AnomalyAlert(
                    crawl_run_id=run_id,
                    source_log_id=source_log_id,
                    alert_type=AlertType.SOURCE_DOWN_3X.value,
                    severity=AlertSeverity.CRITICAL.value,
                    status=AlertStatus.OPEN.value,
                    message=down_msg,
                )
            )
            raised.append(down_msg)
        return raised

    @staticmethod
    def _determine_run_status(
        *,
        sources_queried: int,
        pages_success: int,
        pages_failed: int,
        drift_or_parse_errors: int,
    ) -> CrawlRunStatus:
        if sources_queried == 0:
            return CrawlRunStatus.COMPLETED
        if pages_failed == 0 and drift_or_parse_errors == 0:
            return CrawlRunStatus.COMPLETED
        if (pages_success - drift_or_parse_errors) > 0:
            return CrawlRunStatus.PARTIAL_SUCCESS
        return CrawlRunStatus.FAILED
