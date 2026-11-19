"""PostgreSQL integration tests for PG Advisory Lock, Guardrails quarantine, and CrawlRunner."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from vxg_api.config import ApiSettings
from vxg_api.errors import AppError
from vxg_api.services.admin_service import trigger_crawl_run
from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import (
    AnomalyAlert,
    Commodity,
    CrawlRun,
    CrawlSourceLog,
    Location,
    PriceRecord,
    RawEvidenceSnapshot,
    Source,
)
from vxg_shared.enums import (
    AlertType,
    CrawlRunStatus,
    CrawlScope,
    FetchStatus,
    TriggerType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_content_sha256, compute_price_idempotency_key
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    AdapterRegistry,
    BaseSourceAdapter,
    SchemaDriftError,
)
from vxg_worker.adapters.gold.sjc_json import CAFEF_SJC_FALLBACK_URL, SJC_WAF_FALLBACK_NOTE
from vxg_worker.config import WorkerSettings
from vxg_worker.fetcher.two_tier import TwoTierFetcher
from vxg_worker.fetcher.types import FetcherTier, FetchResult
from vxg_worker.locks import PostgresAdvisoryLock
from vxg_worker.persistence import get_latest_verified_price
from vxg_worker.runner import CrawlRunner, enqueue_crawl_run
from vxg_worker.storage.snapshots import prune_expired_snapshots, store_raw_snapshot
from vxg_worker.validation.guardrails import PriceCandidate

_HOST_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker",
)
_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
_GOLD_FIXTURE_BYTES: dict[str, bytes] = {
    "gold/cafef_gold.json": (_FIXTURES_DIR / "gold/cafef_gold.json").read_bytes(),
    "gold/doji_board.html": (_FIXTURES_DIR / "gold/doji_board.html").read_bytes(),
    "gold/pnj_gold.html": (_FIXTURES_DIR / "gold/pnj_gold.html").read_bytes(),
}


class _StubTwoTierFetcher(TwoTierFetcher):
    def __init__(self, payloads_by_code: dict[str, bytes]) -> None:
        super().__init__()
        self._payloads = payloads_by_code

    async def fetch_source(self, source: Source) -> FetchResult:
        raw = self._payloads.get(
            source.code,
            b"<html><title>Stub</title><body>ok</body></html>",
        )
        return FetchResult(
            request_url=source.endpoint_url,
            final_url=source.endpoint_url,
            http_status=200,
            content_type="application/xml" if source.code == "SJC_OFFICIAL_XML" else "text/html",
            raw_bytes=raw,
            content_sha256=compute_content_sha256(raw),
            byte_size=len(raw),
            response_time_ms=12,
            attempts=1,
            tier_used=FetcherTier.HTTPX,
            fetch_status=FetchStatus.SUCCESS,
        )


class _SyntheticGoldAdapter(BaseSourceAdapter):
    def __init__(self, parser_key: str, mode: str, base_buy: int, base_sell: int) -> None:
        self.parser_key = parser_key
        self._mode = mode
        self._base_buy = base_buy
        self._base_sell = base_sell
        self._fixed_pub_valid = vn_now() - timedelta(minutes=2)
        self._fixed_pub_spike = vn_now() - timedelta(minutes=1)
        self._fixed_crawled = vn_now()

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        if self._mode == "drift":
            raise SchemaDriftError("Simulated canary table missing")

        sha = fetch_result.content_sha256 or compute_content_sha256(fetch_result.raw_bytes)
        if self._mode == "valid":
            buy, sell = self._base_buy, self._base_sell
            pub_dt = self._fixed_pub_valid
        elif self._mode == "spike":
            buy = int(self._base_buy * 1.25)
            sell = int(self._base_sell * 1.25)
            pub_dt = self._fixed_pub_spike
        else:
            raise ValueError(f"Unknown mode {self._mode}")

        candidate = PriceCandidate(
            commodity_code="GOLD_SJC_BAR_SJC",
            location_code="HCM",
            buy_price_vnd=buy,
            sell_price_vnd=sell,
            retail_price_vnd=None,
            effective_date=vn_today(),
            source_published_at=pub_dt,
            crawled_at=self._fixed_crawled,
            source_url=source.endpoint_url,
            evidence_sha256=sha,
        )
        return AdapterParseResult(
            documents_parsed=1,
            candidates=[candidate],
            parser_metadata={"mode": self._mode},
        )


@pytest.mark.asyncio
async def test_postgres_advisory_lock_blocks_concurrent_holder() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    test_lock_key = 908_712_999
    try:
        async with PostgresAdvisoryLock(engine, test_lock_key) as first_acquired:
            assert first_acquired is True
            async with PostgresAdvisoryLock(engine, test_lock_key) as second_acquired:
                assert second_acquired is False

        async with PostgresAdvisoryLock(engine, test_lock_key) as reacquired:
            assert reacquired is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crawl_runner_quarantine_idempotency_and_telemetry_cleanup() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    created_run_ids: list[uuid.UUID] = []
    created_shas: list[str] = []

    try:
        async with session_factory() as session:
            initial_prices = int(
                (await session.execute(select(func.count()).select_from(PriceRecord))).scalar_one()
            )
            gold_sources = list(
                (
                    await session.execute(
                        select(Source).where(Source.category == "GOLD", Source.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )
            original_source_state: dict[int, tuple[Any, int]] = {
                s.id: (s.last_success_at, s.consecutive_failures) for s in gold_sources
            }
            sjc_source = next(s for s in gold_sources if s.code == "SJC_OFFICIAL_XML")
            sjc_comm_id = (
                await session.execute(select(Commodity.id).where(Commodity.code == "GOLD_SJC_BAR_SJC"))
            ).scalar_one()
            hcm_loc_id = (
                await session.execute(select(Location.id).where(Location.code == "HCM"))
            ).scalar_one()
            existing_latest = await get_latest_verified_price(
                session, commodity_id=sjc_comm_id, location_id=hcm_loc_id
            )
            base_buy = (
                (existing_latest.buy_price_vnd + 10_000)
                if existing_latest and existing_latest.buy_price_vnd
                else 133_700_000
            )
            base_sell = (
                (existing_latest.sell_price_vnd + 10_000)
                if existing_latest and existing_latest.sell_price_vnd
                else 136_700_000
            )

        settings = WorkerSettings.model_validate(
            {
                "DATABASE_URL": _HOST_TEST_DB_URL,
                "DATABASE_URL_SYNC": None,
                "advisory_lock_key": 908_712_998,
                "price_spike_threshold_pct": 15.0,
            }
        )

        # 1. Valid crawl inserts 1 VERIFIED record + deduplicated SHA-256 snapshot
        valid_xml = b'<?xml version="1.0"?><root><ratelist id="t1"/></root>'
        created_shas.append(compute_content_sha256(valid_xml))
        reg_valid = AdapterRegistry()
        reg_valid.register(_SyntheticGoldAdapter(sjc_source.parser_key, "valid", base_buy, base_sell))
        runner_valid = CrawlRunner(
            engine=engine,
            session_factory=session_factory,
            settings=settings,
            adapter_registry=reg_valid,
            fetcher=_StubTwoTierFetcher({"SJC_OFFICIAL_XML": valid_xml}),
        )
        sum1 = await runner_valid.execute_run(
            scope=CrawlScope.GOLD,
            trigger_type=TriggerType.MANUAL_ADMIN,
        )
        created_run_ids.append(sum1.run_id)
        assert sum1.status == CrawlRunStatus.COMPLETED.value
        assert sum1.sources_queried_count == len(gold_sources)
        assert sum1.pages_fetched_success == len(gold_sources)
        assert sum1.pages_fetched_failed == 0
        assert sum1.records_verified_count == 1
        assert sum1.records_flagged_count == 0
        assert sum1.records_unchanged_count == 0

        # 2. Identical re-run hits idempotency_key -> UNCHANGED (0 duplicate rows)
        sum2 = await runner_valid.execute_run(
            scope=CrawlScope.GOLD,
            trigger_type=TriggerType.MANUAL_ADMIN,
        )
        created_run_ids.append(sum2.run_id)
        assert sum2.records_verified_count == 0
        assert sum2.records_unchanged_count == 1

        # 3. +25% Spike triggers FLAGGED quarantine + PRICE_SPIKE alert; clean price preserved
        spike_xml = b'<?xml version="1.0"?><root><ratelist id="t2-spike"/></root>'
        created_shas.append(compute_content_sha256(spike_xml))
        reg_spike = AdapterRegistry()
        reg_spike.register(_SyntheticGoldAdapter(sjc_source.parser_key, "spike", base_buy, base_sell))
        runner_spike = CrawlRunner(
            engine=engine,
            session_factory=session_factory,
            settings=settings,
            adapter_registry=reg_spike,
            fetcher=_StubTwoTierFetcher({"SJC_OFFICIAL_XML": spike_xml}),
        )
        sum3 = await runner_spike.execute_run(
            scope=CrawlScope.GOLD,
            trigger_type=TriggerType.MANUAL_ADMIN,
        )
        created_run_ids.append(sum3.run_id)
        assert sum3.records_verified_count == 0
        assert sum3.records_flagged_count == 1

        async with session_factory() as session:
            # Latest verified MUST still be base_buy / base_sell, NEVER overwritten by +25% spike
            sjc_row = (
                await session.execute(select(Source).where(Source.code == "SJC_OFFICIAL_XML"))
            ).scalar_one()
            latest = await get_latest_verified_price(
                session,
                commodity_id=sjc_comm_id,
                location_id=hcm_loc_id,
            )
            assert latest is not None
            assert latest.buy_price_vnd == base_buy
            assert latest.sell_price_vnd == base_sell

            flagged_record = (
                await session.execute(
                    select(PriceRecord).where(PriceRecord.crawl_run_id == sum3.run_id)
                )
            ).scalar_one()
            assert flagged_record.verification_status == VerificationStatus.FLAGGED.value

            spike_alert = (
                await session.execute(
                    select(AnomalyAlert).where(AnomalyAlert.price_record_id == flagged_record.id)
                )
            ).scalar_one()
            assert spike_alert.alert_type == AlertType.PRICE_SPIKE.value
            assert sjc_row.consecutive_failures == 0

        # 4. Schema drift + QUEUED poll_once execution
        queued_run = await enqueue_crawl_run(
            session_factory,
            scope=CrawlScope.GOLD,
            trigger_type=TriggerType.MANUAL_ADMIN,
        )
        created_run_ids.append(queued_run.id)

        reg_drift = AdapterRegistry()
        reg_drift.register(_SyntheticGoldAdapter(sjc_source.parser_key, "drift", base_buy, base_sell))
        runner_drift = CrawlRunner(
            engine=engine,
            session_factory=session_factory,
            settings=settings,
            adapter_registry=reg_drift,
            fetcher=_StubTwoTierFetcher({"SJC_OFFICIAL_XML": valid_xml}),
        )
        sum4 = await runner_drift.poll_once()
        assert sum4 is not None
        assert sum4.run_id == queued_run.id
        assert sum4.status == CrawlRunStatus.PARTIAL_SUCCESS.value
        assert any(
            log.source_code == "SJC_OFFICIAL_XML"
            and log.fetch_status == FetchStatus.SCHEMA_DRIFT.value
            for log in sum4.source_logs
        )

    finally:
        # Remove ALL synthetic test artifacts so DB stays 100% free of fake price records
        async with session_factory() as cleanup:
            if created_run_ids:
                await cleanup.execute(
                    delete(AnomalyAlert).where(AnomalyAlert.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(
                    delete(PriceRecord).where(PriceRecord.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(
                    delete(CrawlSourceLog).where(
                        CrawlSourceLog.crawl_run_id.in_(created_run_ids)
                    )
                )
                await cleanup.execute(delete(CrawlRun).where(CrawlRun.id.in_(created_run_ids)))
            if created_shas:
                await cleanup.execute(
                    delete(RawEvidenceSnapshot).where(
                        RawEvidenceSnapshot.content_sha256.in_(created_shas)
                    )
                )
            stub_sha = compute_content_sha256(b"<html><title>Stub</title><body>ok</body></html>")
            await cleanup.execute(
                delete(RawEvidenceSnapshot).where(RawEvidenceSnapshot.content_sha256 == stub_sha)
            )
            for src_id, (prev_last, prev_fail) in original_source_state.items():
                await cleanup.execute(
                    update(Source)
                    .where(Source.id == src_id)
                    .values(last_success_at=prev_last, consecutive_failures=prev_fail)
                )
            await cleanup.commit()

            remaining_prices = int(
                (await cleanup.execute(select(func.count()).select_from(PriceRecord))).scalar_one()
            )
            assert remaining_prices == initial_prices
        await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_retention_prunes_expired_unprotected_only() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    run_id: uuid.UUID | None = None
    price_id: uuid.UUID | None = None
    old_unreferenced_sha: str | None = None
    old_protected_sha: str | None = None

    try:
        async with session_factory() as session:
            sjc_comm_id = (
                await session.execute(select(Commodity.id).where(Commodity.code == "GOLD_SJC_BAR_SJC"))
            ).scalar_one()
            hcm_loc_id = (
                await session.execute(select(Location.id).where(Location.code == "HCM"))
            ).scalar_one()
            sjc_src_id = (
                await session.execute(select(Source.id).where(Source.code == "SJC_OFFICIAL_XML"))
            ).scalar_one()

            old_time = vn_now() - timedelta(days=120)
            future_eff_date = vn_today() + timedelta(days=1)
            future_pub = vn_now() + timedelta(days=1)

            expired_id, old_unreferenced_sha = await store_raw_snapshot(
                session,
                raw_bytes=b"<html><body>expired-snapshot-retention-test</body></html>",
                content_type="text/html",
            )
            protected_id, old_protected_sha = await store_raw_snapshot(
                session,
                raw_bytes=b"<html><body>protected-latest-verified-snapshot-test</body></html>",
                content_type="text/html",
            )
            await session.execute(
                update(RawEvidenceSnapshot)
                .where(RawEvidenceSnapshot.id.in_([expired_id, protected_id]))
                .values(first_seen_at=old_time, last_seen_at=old_time)
            )

            run = CrawlRun(
                run_code=f"RUN-RETENTION-{uuid.uuid4().hex[:8].upper()}",
                scope_category=CrawlScope.GOLD.value,
                trigger_type=TriggerType.MANUAL_ADMIN.value,
                status=CrawlRunStatus.COMPLETED.value,
                started_at=vn_now(),
            )
            session.add(run)
            await session.flush()
            run_id = run.id

            log = CrawlSourceLog(
                crawl_run_id=run_id,
                source_id=sjc_src_id,
                snapshot_id=expired_id,
                request_url="https://sjc.com.vn/retention-test",
                final_url="https://sjc.com.vn/retention-test",
                http_status=200,
                fetch_strategy_used="HTTPX",
                response_time_ms=15,
                attempt_number=1,
                fetch_status=FetchStatus.SUCCESS.value,
                content_sha256=old_unreferenced_sha,
            )
            session.add(log)
            await session.flush()

            price = PriceRecord(
                commodity_id=sjc_comm_id,
                location_id=hcm_loc_id,
                source_id=sjc_src_id,
                crawl_run_id=run_id,
                source_log_id=log.id,
                buy_price_vnd=133_500_000,
                sell_price_vnd=136_500_000,
                retail_price_vnd=None,
                effective_date=future_eff_date,
                source_published_at=future_pub,
                crawled_at=future_pub,
                last_verified_at=future_pub,
                source_url="https://sjc.com.vn/retention-test",
                evidence_sha256=old_protected_sha,
                verification_status=VerificationStatus.VERIFIED.value,
                validation_notes={"test": "retention"},
                idempotency_key=compute_price_idempotency_key(
                    commodity_id=sjc_comm_id,
                    location_id=hcm_loc_id,
                    source_id=sjc_src_id,
                    effective_date=future_eff_date,
                    source_published_at=future_pub,
                    buy_price_vnd=133_500_000,
                    sell_price_vnd=136_500_000,
                    retail_price_vnd=None,
                ),
            )
            session.add(price)
            await session.flush()
            price_id = price.id
            await session.commit()

            prune_res = await prune_expired_snapshots(session, retention_days=90)
            await session.commit()

            assert prune_res.deleted_count >= 1
            assert prune_res.freed_bytes >= len(
                b"<html><body>expired-snapshot-retention-test</body></html>"
            )

        async with session_factory() as verify_session:
            expired_row = await verify_session.get(RawEvidenceSnapshot, expired_id)
            protected_row = await verify_session.get(RawEvidenceSnapshot, protected_id)
            refreshed_log = await verify_session.get(CrawlSourceLog, log.id)
            kept_price = await verify_session.get(PriceRecord, price_id)

            assert expired_row is None
            assert protected_row is not None
            assert refreshed_log is not None and refreshed_log.snapshot_id is None
            assert kept_price is not None
    finally:
        async with session_factory() as cleanup:
            if price_id is not None:
                await cleanup.execute(delete(PriceRecord).where(PriceRecord.id == price_id))
            if run_id is not None:
                await cleanup.execute(
                    delete(CrawlSourceLog).where(CrawlSourceLog.crawl_run_id == run_id)
                )
                await cleanup.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
            shas = [s for s in (old_unreferenced_sha, old_protected_sha) if s is not None]
            if shas:
                await cleanup.execute(
                    delete(RawEvidenceSnapshot).where(
                        RawEvidenceSnapshot.content_sha256.in_(shas)
                    )
                )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_github_dispatch_failure_marks_failed_and_cli_run_id_executes_queued() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    created_run_ids: list[uuid.UUID] = []
    created_shas: list[str] = []
    original_source_state: dict[int, tuple[Any, int]] = {}

    gh_settings = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": _HOST_TEST_DB_URL,
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": "vxg_prod_9f8e7d6c5b4a3210fedcba987654",
            "WORKER_DISPATCH_MODE": "github_actions",
            "GITHUB_REPO": "acme/vxg-tracker",
            "GITHUB_WORKFLOW_FILE": "crawl-schedule.yml",
            "GITHUB_WORKFLOW_REF": "main",
            "GITHUB_DISPATCH_TOKEN": "ghp_test_token",
        }
    )

    try:
        async with session_factory() as session:
            gold_sources = list(
                (
                    await session.execute(
                        select(Source).where(Source.category == "GOLD", Source.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )
            original_source_state = {
                s.id: (s.last_success_at, s.consecutive_failures) for s in gold_sources
            }
            sjc_source = next(s for s in gold_sources if s.code == "SJC_OFFICIAL_XML")
            sjc_comm_id = (
                await session.execute(select(Commodity.id).where(Commodity.code == "GOLD_SJC_BAR_SJC"))
            ).scalar_one()
            hcm_loc_id = (
                await session.execute(select(Location.id).where(Location.code == "HCM"))
            ).scalar_one()
            existing_latest = await get_latest_verified_price(
                session, commodity_id=sjc_comm_id, location_id=hcm_loc_id
            )
            base_buy = (
                (existing_latest.buy_price_vnd + 10_000)
                if existing_latest and existing_latest.buy_price_vnd
                else 133_700_000
            )
            base_sell = (
                (existing_latest.sell_price_vnd + 10_000)
                if existing_latest and existing_latest.sell_price_vnd
                else 136_700_000
            )

        # Hold worker default lock so live vxg_worker defers QUEUED test rows until cleanup.
        async with PostgresAdvisoryLock(engine, 908_712_001) as guard_acquired:
            assert guard_acquired is True

            # 1. Dispatch failure (HTTP 500) MUST mark CrawlRun FAILED (zero stuck QUEUED jobs)
            async with (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda _req: httpx.Response(500, text="GitHub down")
                    )
                ) as fail_client,
                session_factory() as session,
            ):
                with pytest.raises(AppError) as err_info:
                    await trigger_crawl_run(
                        session,
                        scope=CrawlScope.GOLD,
                        settings=gh_settings,
                        http_client=fail_client,
                    )
                assert err_info.value.status_code == 502
                assert err_info.value.code == "GITHUB_DISPATCH_FAILED"
                assert isinstance(err_info.value.details, dict)
                failed_run_id = uuid.UUID(str(err_info.value.details["run_id"]))
                created_run_ids.append(failed_run_id)

            async with session_factory() as verify_session:
                failed_row = await verify_session.get(CrawlRun, failed_run_id)
                assert failed_row is not None
                assert failed_row.status == CrawlRunStatus.FAILED.value
                assert failed_row.error_summary is not None
                assert "HTTP 500" in failed_row.error_summary

            # 2. Successful dispatch (HTTP 204) creates QUEUED run; runner executes exact --run-id
            async with (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda _req: httpx.Response(204))
                ) as ok_client,
                session_factory() as session,
            ):
                dispatched = await trigger_crawl_run(
                    session,
                    scope=CrawlScope.GOLD,
                    settings=gh_settings,
                    http_client=ok_client,
                )
                assert dispatched.status == CrawlRunStatus.QUEUED
                created_run_ids.append(dispatched.run_id)

            valid_xml = b'<?xml version="1.0"?><root><ratelist id="run-id-test"/></root>'
            created_shas.append(compute_content_sha256(valid_xml))
            reg = AdapterRegistry()
            reg.register(_SyntheticGoldAdapter(sjc_source.parser_key, "valid", base_buy, base_sell))
            worker_settings = WorkerSettings.model_validate(
                {
                    "DATABASE_URL": _HOST_TEST_DB_URL,
                    "DATABASE_URL_SYNC": None,
                    "advisory_lock_key": 908_712_996,
                }
            )
            runner = CrawlRunner(
                engine=engine,
                session_factory=session_factory,
                settings=worker_settings,
                adapter_registry=reg,
                fetcher=_StubTwoTierFetcher({"SJC_OFFICIAL_XML": valid_xml}),
            )
            run_summary = await runner.execute_run(
                scope=CrawlScope.GOLD,
                trigger_type=TriggerType.MANUAL_ADMIN,
                existing_run_id=dispatched.run_id,
            )
            assert run_summary.run_id == dispatched.run_id
            assert run_summary.status == CrawlRunStatus.COMPLETED.value
            assert run_summary.records_verified_count == 1
    finally:
        async with session_factory() as cleanup:
            if created_run_ids:
                await cleanup.execute(
                    delete(AnomalyAlert).where(AnomalyAlert.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(
                    delete(PriceRecord).where(PriceRecord.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(
                    delete(CrawlSourceLog).where(CrawlSourceLog.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(delete(CrawlRun).where(CrawlRun.id.in_(created_run_ids)))
            if created_shas:
                await cleanup.execute(
                    delete(RawEvidenceSnapshot).where(
                        RawEvidenceSnapshot.content_sha256.in_(created_shas)
                    )
                )
            stub_sha = compute_content_sha256(b"<html><title>Stub</title><body>ok</body></html>")
            await cleanup.execute(
                delete(RawEvidenceSnapshot).where(RawEvidenceSnapshot.content_sha256 == stub_sha)
            )
            for src_id, (prev_last, prev_fail) in original_source_state.items():
                await cleanup.execute(
                    update(Source)
                    .where(Source.id == src_id)
                    .values(last_success_at=prev_last, consecutive_failures=prev_fail)
                )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_queued_run_auto_expires_and_unblocks_manual_trigger() -> None:
    """Stale QUEUED CrawlRun (>15m) expires to FAILED so manual triggers are never deadlocked."""
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    created_run_ids: list[uuid.UUID] = []

    try:
        async with PostgresAdvisoryLock(engine, 908_712_001):
            now_vn = vn_now()
            stale_gold_id = uuid.uuid4()
            stale_fuel_id = uuid.uuid4()
            created_run_ids.extend([stale_gold_id, stale_fuel_id])

            async with session_factory() as seed_session:
                seed_session.add(
                    CrawlRun(
                        id=stale_gold_id,
                        run_code=f"test-stale-gold-{stale_gold_id.hex[:8]}",
                        scope_category=CrawlScope.GOLD.value,
                        trigger_type=TriggerType.MANUAL_ADMIN.value,
                        status=CrawlRunStatus.QUEUED.value,
                        started_at=now_vn - timedelta(minutes=16),
                    )
                )
                await seed_session.commit()

            # 1) Stale GOLD run (>15m) expires cleanly and allows a new GOLD enqueue.
            async with session_factory() as api_session:
                triggered = await trigger_crawl_run(api_session, scope=CrawlScope.GOLD)
            created_run_ids.append(triggered.run_id)

            assert triggered.run_id != stale_gold_id
            assert triggered.status == CrawlRunStatus.QUEUED
            assert triggered.scope == CrawlScope.GOLD

            async with session_factory() as verify_session:
                expired_gold = await verify_session.get(CrawlRun, stale_gold_id)
                assert expired_gold is not None
                assert expired_gold.status == CrawlRunStatus.FAILED.value
                assert expired_gold.error_summary == "Timed out waiting in QUEUED state (>15m)"
                assert expired_gold.finished_at is not None
                assert expired_gold.duration_ms is not None
                assert expired_gold.duration_ms >= 15 * 60 * 1000

            # 2) Even if a fresh (<15m) overlapping run raises 409, any coexisting stale QUEUED
            #    run encountered during the check is still persisted as FAILED.
            async with session_factory() as seed_session:
                seed_session.add(
                    CrawlRun(
                        id=stale_fuel_id,
                        run_code=f"test-stale-fuel-{stale_fuel_id.hex[:8]}",
                        scope_category=CrawlScope.FUEL.value,
                        trigger_type=TriggerType.MANUAL_ADMIN.value,
                        status=CrawlRunStatus.QUEUED.value,
                        started_at=now_vn - timedelta(minutes=20),
                    )
                )
                await seed_session.commit()

            async with session_factory() as api_session:
                with pytest.raises(AppError) as exc_info:
                    await trigger_crawl_run(api_session, scope=CrawlScope.ALL)

            assert exc_info.value.status_code == 409
            assert exc_info.value.code == "CRAWL_RUN_ALREADY_ACTIVE"
            assert exc_info.value.details is not None
            assert exc_info.value.details["active_run_id"] == str(triggered.run_id)

            async with session_factory() as verify_session:
                expired_fuel = await verify_session.get(CrawlRun, stale_fuel_id)
                fresh_gold = await verify_session.get(CrawlRun, triggered.run_id)
                assert expired_fuel is not None
                assert expired_fuel.status == CrawlRunStatus.FAILED.value
                assert expired_fuel.error_summary == "Timed out waiting in QUEUED state (>15m)"
                assert expired_fuel.finished_at is not None
                assert fresh_gold is not None
                assert fresh_gold.status == CrawlRunStatus.QUEUED.value
    finally:
        if created_run_ids:
            async with session_factory() as cleanup:
                await cleanup.execute(
                    delete(CrawlRun).where(CrawlRun.id.in_(created_run_ids))
                )
                await cleanup.commit()
        await engine.dispose()


class _SjcBlockedGoldenFetcher(TwoTierFetcher):
    """Test fetcher returning HTTP 403 for SJC primary endpoint and golden fixtures elsewhere."""

    def __init__(self, gold_fixtures: dict[str, bytes]) -> None:
        super().__init__()
        self._gold_fixtures = gold_fixtures
        self._cafef_bytes = gold_fixtures["gold/cafef_gold.json"]
        self._cafef_sha = compute_content_sha256(self._cafef_bytes)

    async def fetch_source(self, source: Source) -> FetchResult:
        if source.code == "SJC_OFFICIAL_XML":
            blocked_html = b"<html><title>403 Forbidden</title><body>WAF Block</body></html>"
            return FetchResult(
                request_url=source.endpoint_url,
                final_url=source.endpoint_url,
                http_status=403,
                content_type="text/html; charset=utf-8",
                raw_bytes=blocked_html,
                content_sha256=compute_content_sha256(blocked_html),
                byte_size=len(blocked_html),
                response_time_ms=90,
                attempts=1,
                tier_used=FetcherTier.PLAYWRIGHT,
                fetch_status=FetchStatus.BLOCKED,
                fallback_triggered=True,
                error_code="HTTP_403",
                error_message="HTTP 403 blocked by SJC WAF",
            )

        fixture_map = {
            "DOJI_BOARD": ("gold/doji_board.html", "text/html; charset=utf-8"),
            "PNJ_HTML": ("gold/pnj_gold.html", "text/html; charset=utf-8"),
            "CAFEF_GOLD_CROSSCHECK": ("gold/cafef_gold.json", "application/json; charset=utf-8"),
        }
        rel_path, content_type = fixture_map[source.code]
        raw = self._gold_fixtures[rel_path]
        return FetchResult(
            request_url=source.endpoint_url,
            final_url=source.endpoint_url,
            http_status=200,
            content_type=content_type,
            raw_bytes=raw,
            content_sha256=compute_content_sha256(raw),
            byte_size=len(raw),
            response_time_ms=25,
            attempts=1,
            tier_used=FetcherTier.HTTPX,
            fetch_status=FetchStatus.SUCCESS,
        )

    async def fetch_url(
        self,
        url: str,
        *,
        fetch_strategy: str,
        timeout_ms: int,
        rate_limit_delay_ms: int,
        referer: str | None = None,
    ) -> FetchResult:
        assert url == CAFEF_SJC_FALLBACK_URL
        return FetchResult(
            request_url=url,
            final_url=url,
            http_status=200,
            content_type="application/json; charset=utf-8",
            raw_bytes=self._cafef_bytes,
            content_sha256=self._cafef_sha,
            byte_size=len(self._cafef_bytes),
            response_time_ms=35,
            attempts=1,
            tier_used=FetcherTier.HTTPX,
            fetch_status=FetchStatus.SUCCESS,
        )


@pytest.mark.asyncio
async def test_sjc_waf_fallback_on_403_persists_cafef_snapshot_and_audit_evidence() -> None:
    """When SJC_OFFICIAL_XML hits 403, worker falls back to CafeF with real SHA-256 & URL."""
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    cafef_sha = compute_content_sha256(_GOLD_FIXTURE_BYTES["gold/cafef_gold.json"])

    created_run_ids: list[uuid.UUID] = []
    candidate_shas: set[str] = {cafef_sha}
    original_source_state: dict[int, tuple[Any, int]] = {}

    async with session_factory() as setup_session:
        gold_sources = (
            await setup_session.execute(select(Source).where(Source.category == "GOLD"))
        ).scalars().all()
        for src in gold_sources:
            original_source_state[src.id] = (src.last_success_at, src.consecutive_failures)
        sjc_src = next(s for s in gold_sources if s.code == "SJC_OFFICIAL_XML")

    for rel in ("gold/doji_board.html", "gold/pnj_gold.html"):
        candidate_shas.add(compute_content_sha256(_GOLD_FIXTURE_BYTES[rel]))

    worker_settings = WorkerSettings.model_validate(
        {
            "DATABASE_URL": _HOST_TEST_DB_URL,
            "DATABASE_URL_SYNC": None,
            "advisory_lock_key": 908_712_995,
            "price_spike_threshold_pct": 50.0,
        }
    )

    try:
        runner = CrawlRunner(
            engine=engine,
            session_factory=session_factory,
            settings=worker_settings,
            fetcher=_SjcBlockedGoldenFetcher(_GOLD_FIXTURE_BYTES),
        )
        summary = await runner.execute_run(
            scope=CrawlScope.GOLD,
            trigger_type=TriggerType.MANUAL_ADMIN,
        )
        created_run_ids.append(summary.run_id)

        assert summary.status == CrawlRunStatus.COMPLETED.value
        assert summary.pages_fetched_failed == 0

        sjc_summary = next(s for s in summary.source_logs if s.source_code == "SJC_OFFICIAL_XML")
        assert sjc_summary.fetch_status == FetchStatus.SUCCESS.value
        assert sjc_summary.http_status == 200
        assert sjc_summary.content_sha256 == cafef_sha
        assert sjc_summary.fallback_triggered is True

        async with session_factory() as verify_session:
            sjc_log = (
                await verify_session.execute(
                    select(CrawlSourceLog).where(
                        CrawlSourceLog.crawl_run_id == summary.run_id,
                        CrawlSourceLog.source_id == sjc_src.id,
                    )
                )
            ).scalar_one()

            assert sjc_log.fetch_status == FetchStatus.SUCCESS.value
            assert sjc_log.http_status == 200
            assert sjc_log.final_url == CAFEF_SJC_FALLBACK_URL
            assert sjc_log.content_sha256 == cafef_sha
            assert sjc_log.snapshot_id is not None
            assert sjc_log.parser_metadata["waf_fallback_triggered"] is True
            assert sjc_log.parser_metadata["waf_fallback_provider"] == "CAFEF_GOLD_CROSSCHECK"
            assert sjc_log.parser_metadata["waf_fallback_url"] == CAFEF_SJC_FALLBACK_URL
            assert sjc_log.parser_metadata["source_detail"] == SJC_WAF_FALLBACK_NOTE
            assert sjc_log.parser_metadata["primary_blocked_attempt"]["http_status"] == 403

            snapshot = await verify_session.get(RawEvidenceSnapshot, sjc_log.snapshot_id)
            assert snapshot is not None
            assert snapshot.content_sha256 == cafef_sha

            sjc_prices = (
                await verify_session.execute(
                    select(PriceRecord).where(
                        PriceRecord.crawl_run_id == summary.run_id,
                        PriceRecord.source_id == sjc_src.id,
                    )
                )
            ).scalars().all()
            assert len(sjc_prices) == 3

            for rec in sjc_prices:
                assert rec.source_url == CAFEF_SJC_FALLBACK_URL
                assert rec.evidence_sha256 == cafef_sha
                assert rec.verification_status == VerificationStatus.VERIFIED.value
                assert rec.cross_check_source_id is None
                assert rec.validation_notes["source_detail"] == SJC_WAF_FALLBACK_NOTE
                assert rec.validation_notes["waf_fallback"]["fallback_url"] == CAFEF_SJC_FALLBACK_URL
                assert rec.validation_notes["waf_fallback"]["fallback_sha256"] == cafef_sha
    finally:
        async with session_factory() as cleanup:
            if created_run_ids:
                await cleanup.execute(
                    delete(AnomalyAlert).where(AnomalyAlert.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(
                    delete(PriceRecord).where(PriceRecord.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(
                    delete(CrawlSourceLog).where(CrawlSourceLog.crawl_run_id.in_(created_run_ids))
                )
                await cleanup.execute(delete(CrawlRun).where(CrawlRun.id.in_(created_run_ids)))
            await cleanup.execute(
                delete(RawEvidenceSnapshot).where(
                    RawEvidenceSnapshot.content_sha256.in_(candidate_shas),
                    ~RawEvidenceSnapshot.id.in_(
                        select(CrawlSourceLog.snapshot_id).where(
                            CrawlSourceLog.snapshot_id.is_not(None)
                        )
                    ),
                )
            )
            for src_id, (prev_last, prev_fail) in original_source_state.items():
                await cleanup.execute(
                    update(Source)
                    .where(Source.id == src_id)
                    .values(last_success_at=prev_last, consecutive_failures=prev_fail)
                )
            await cleanup.commit()
        await engine.dispose()




