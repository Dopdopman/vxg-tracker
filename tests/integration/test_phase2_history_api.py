"""PostgreSQL integration tests for Milestone 7 schema, LIVE_ROLLUP, backfill, and History API."""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import delete, func, select, text

from vxg_api.config import ApiSettings
from vxg_api.main import create_app
from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import (
    CrawlRun,
    CrawlSourceLog,
    DailyPriceSeries,
    PriceRecord,
    RawEvidenceSnapshot,
    Source,
)
from vxg_shared.enums import (
    CrawlRunStatus,
    CrawlScope,
    FetchStatus,
    SeriesOrigin,
    TriggerType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_content_sha256, compute_price_idempotency_key
from vxg_shared.timezone import VN_TZ_NAME, vn_now, vn_today
from vxg_worker.history.backfill_gold import parse_cafef_gold_history
from vxg_worker.history.rollup import (
    persist_backfill_points,
    rollup_daily_from_price_records,
)
from vxg_worker.persistence import load_catalog_lookup

_HOST_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker",
)
_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
_CAFEF_GOLD_BYTES = (_FIXTURES_DIR / "gold/cafef_gold.json").read_bytes()


def _make_api_settings() -> ApiSettings:
    return ApiSettings.model_validate(
        {
            "DATABASE_URL": _HOST_TEST_DB_URL,
            "DATABASE_URL_SYNC": None,
            "CORS_ORIGINS": "http://localhost:3000",
            "ADMIN_API_KEY": "test-milestone7-admin-key-secret",
        }
    )


@pytest.mark.asyncio
async def test_phase2_schema_all_13_tables_and_indexes_present() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' AND tablename != 'alembic_version'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            indexes = set(
                (
                    await conn.execute(
                        text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                    )
                )
                .scalars()
                .all()
            )
        expected_tables = {
            "locations",
            "commodities",
            "sources",
            "crawl_runs",
            "raw_evidence_snapshots",
            "crawl_source_logs",
            "price_records",
            "anomaly_alerts",
            "daily_price_series",
            "macro_indicators",
            "market_news_signals",
            "price_forecasts",
            "forecast_evaluations",
        }
        assert expected_tables.issubset(tables)
        assert {
            "idx_daily_price_series_lookup",
            "idx_daily_price_series_adj",
            "idx_macro_indicators_code_date_desc",
            "idx_market_news_domain_pub_desc",
            "idx_price_forecasts_latest",
            "idx_forecast_evaluations_target_desc",
        }.issubset(indexes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_rollup_wins_over_backfill_and_history_api_endpoints() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)

    # Use synthetic PNJ + FUEL dates inside recent 30d so they stay cleanly isolated from real rows
    # while snapshot/restoring any pre-existing DailyPriceSeries records on those exact keys.
    today = vn_today()
    d_prev = today - timedelta(days=5)
    d_curr = today - timedelta(days=4)

    created_run_ids: list[uuid.UUID] = []
    created_shas: list[str] = []
    backup_daily_rows: list[dict[str, Any]] = []
    touched_daily_keys: list[tuple[int, int, date]] = []

    try:
        async with session_factory() as session:
            catalog = await load_catalog_lookup(session)
            sjc_bar = catalog.commodities_by_code["GOLD_SJC_BAR_SJC"]
            pnj_bar = catalog.commodities_by_code["GOLD_SJC_BAR_PNJ"]
            ron95 = catalog.commodities_by_code["FUEL_RON95_III"]
            hcm = catalog.locations_by_code["HCM"]
            vung1 = catalog.locations_by_code["VUNG_1"]
            sjc_src = (
                await session.execute(select(Source).where(Source.code == "SJC_OFFICIAL_XML"))
            ).scalar_one()

            # 1) Verify Phase 1 audit counts remain untouched by backfill persistence
            before_pr_count = int((await session.execute(select(func.count(PriceRecord.id)))).scalar_one())
            before_run_count = int((await session.execute(select(func.count(CrawlRun.id)))).scalar_one())

            cafef_pts = parse_cafef_gold_history(
                _CAFEF_GOLD_BYTES,
                months=24,
                as_of_date=date(2026, 11, 20),
            )
            sample_pt = cafef_pts[-1]
            assert sample_pt.location_code == "HCM"

            # Backup existing rows for all keys we will touch in this test
            candidate_keys = [
                (pnj_bar.id, hcm.id, d_prev),
                (pnj_bar.id, hcm.id, d_curr),
                (ron95.id, vung1.id, d_prev),
                (ron95.id, vung1.id, d_curr),
                (sjc_bar.id, hcm.id, sample_pt.price_date),
            ]
            touched_daily_keys.extend(candidate_keys)
            existing_rows = (
                await session.execute(
                    select(DailyPriceSeries).where(
                        DailyPriceSeries.commodity_id.in_({k[0] for k in candidate_keys}),
                        DailyPriceSeries.location_id.in_({k[1] for k in candidate_keys}),
                        DailyPriceSeries.price_date.in_({k[2] for k in candidate_keys}),
                    )
                )
            ).scalars().all()
            existing_by_key = {
                (r.commodity_id, r.location_id, r.price_date): r for r in existing_rows
            }
            for k in candidate_keys:
                row = existing_by_key.get(k)
                if row is not None:
                    backup_daily_rows.append(
                        {
                            "commodity_id": row.commodity_id,
                            "location_id": row.location_id,
                            "price_date": row.price_date,
                            "buy_price_vnd": row.buy_price_vnd,
                            "sell_price_vnd": row.sell_price_vnd,
                            "retail_price_vnd": row.retail_price_vnd,
                            "reference_price_vnd": row.reference_price_vnd,
                            "series_origin": row.series_origin,
                            "is_adjustment_day": row.is_adjustment_day,
                            "source_record_id": row.source_record_id,
                            "source_code": row.source_code,
                            "source_url": row.source_url,
                            "evidence_sha256": row.evidence_sha256,
                            "metadata_json": dict(row.metadata_json),
                            "updated_at": row.updated_at,
                        }
                    )

            # Seed BACKFILL points for SJC sample date, PNJ (d_prev, d_curr), and FUEL (d_prev, d_curr)
            from vxg_worker.history.rollup import BackfillPricePoint

            test_points = [
                sample_pt,
                BackfillPricePoint(
                    commodity_code="GOLD_SJC_BAR_PNJ",
                    location_code="HCM",
                    price_date=d_prev,
                    buy_price_vnd=132_000_000,
                    sell_price_vnd=135_000_000,
                    retail_price_vnd=None,
                    is_adjustment_day=True,
                    source_code="TEST_GOLD_BACKFILL",
                    source_url="https://example.invalid/pnj-prev",
                ),
                BackfillPricePoint(
                    commodity_code="GOLD_SJC_BAR_PNJ",
                    location_code="HCM",
                    price_date=d_curr,
                    buy_price_vnd=132_500_000,
                    sell_price_vnd=135_500_000,
                    retail_price_vnd=None,
                    is_adjustment_day=True,
                    source_code="TEST_GOLD_BACKFILL",
                    source_url="https://example.invalid/pnj-curr",
                ),
                BackfillPricePoint(
                    commodity_code="FUEL_RON95_III",
                    location_code="VUNG_1",
                    price_date=d_prev,
                    buy_price_vnd=None,
                    sell_price_vnd=None,
                    retail_price_vnd=21_000,
                    is_adjustment_day=True,
                    source_code="TEST_FUEL_BACKFILL",
                    source_url="https://example.invalid/fuel-prev",
                ),
                BackfillPricePoint(
                    commodity_code="FUEL_RON95_III",
                    location_code="VUNG_1",
                    price_date=d_curr,
                    buy_price_vnd=None,
                    sell_price_vnd=None,
                    retail_price_vnd=21_500,
                    is_adjustment_day=True,
                    source_code="TEST_FUEL_BACKFILL",
                    source_url="https://example.invalid/fuel-curr",
                ),
            ]
            # Remove pre-existing LIVE_ROLLUP on d_curr if any so test controls d_prev/d_curr cleanly
            await session.execute(
                delete(DailyPriceSeries).where(
                    DailyPriceSeries.commodity_id.in_([pnj_bar.id, ron95.id]),
                    DailyPriceSeries.price_date.in_([d_prev, d_curr]),
                )
            )
            await persist_backfill_points(session, test_points, catalog)
            await session.commit()

            after_pr_count = int((await session.execute(select(func.count(PriceRecord.id)))).scalar_one())
            after_run_count = int((await session.execute(select(func.count(CrawlRun.id)))).scalar_one())
            assert after_pr_count == before_pr_count
            assert after_run_count == before_run_count

            # 2) Create a real Phase 1 PriceRecord on d_curr for GOLD_SJC_BAR_PNJ HCM -> LIVE_ROLLUP overrides BACKFILL
            raw_bytes = b'{"test": "live-rollup-override"}'
            sha = compute_content_sha256(raw_bytes)
            created_shas.append(sha)
            existing_snap = (
                await session.execute(
                    select(RawEvidenceSnapshot).where(RawEvidenceSnapshot.content_sha256 == sha)
                )
            ).scalar_one_or_none()
            if existing_snap is None:
                snap = RawEvidenceSnapshot(
                    content_sha256=sha,
                    content_type="application/json",
                    byte_size=len(raw_bytes),
                    compressed_payload=raw_bytes,
                )
                session.add(snap)
                await session.flush()
                snap_id = snap.id
            else:
                snap_id = existing_snap.id

            run_row = CrawlRun(
                run_code=f"TEST-M7-{uuid.uuid4().hex[:8].upper()}",
                scope_category=CrawlScope.GOLD.value,
                trigger_type=TriggerType.MANUAL_ADMIN.value,
                status=CrawlRunStatus.COMPLETED.value,
                started_at=vn_now(),
                finished_at=vn_now(),
            )
            session.add(run_row)
            await session.flush()
            created_run_ids.append(run_row.id)

            src_log = CrawlSourceLog(
                crawl_run_id=run_row.id,
                source_id=sjc_src.id,
                snapshot_id=snap_id,
                request_url="https://example.invalid/live-sjc",
                final_url="https://example.invalid/live-sjc",
                http_status=200,
                fetch_strategy_used="HTTP_JSON",
                fetch_status=FetchStatus.SUCCESS.value,
                content_sha256=sha,
            )
            session.add(src_log)
            await session.flush()

            live_pub = vn_now() - timedelta(days=4)
            live_pr = PriceRecord(
                commodity_id=pnj_bar.id,
                location_id=hcm.id,
                source_id=sjc_src.id,
                crawl_run_id=run_row.id,
                source_log_id=src_log.id,
                buy_price_vnd=133_100_000,
                sell_price_vnd=136_100_000,
                retail_price_vnd=None,
                effective_date=d_curr,
                source_published_at=live_pub,
                crawled_at=vn_now(),
                last_verified_at=vn_now(),
                source_url="https://example.invalid/live-pnj",
                evidence_sha256=sha,
                verification_status=VerificationStatus.VERIFIED.value,
                idempotency_key=compute_price_idempotency_key(
                    commodity_id=pnj_bar.id,
                    location_id=hcm.id,
                    source_id=sjc_src.id,
                    effective_date=d_curr,
                    source_published_at=live_pub,
                    buy_price_vnd=133_100_000,
                    sell_price_vnd=136_100_000,
                    retail_price_vnd=None,
                ),
            )
            session.add(live_pr)
            await session.flush()

            rolled = await rollup_daily_from_price_records(session, crawl_run_id=run_row.id)
            assert rolled >= 1
            await session.commit()

            # Verify LIVE_ROLLUP overwrote BACKFILL on d_curr
            rolled_row = (
                await session.execute(
                    select(DailyPriceSeries).where(
                        DailyPriceSeries.commodity_id == pnj_bar.id,
                        DailyPriceSeries.location_id == hcm.id,
                        DailyPriceSeries.price_date == d_curr,
                    )
                )
            ).scalar_one()
            assert rolled_row.series_origin == SeriesOrigin.LIVE_ROLLUP.value
            assert rolled_row.sell_price_vnd == 136_100_000
            assert rolled_row.source_record_id == live_pr.id

            # Running BACKFILL again for d_curr MUST NOT overwrite LIVE_ROLLUP
            await persist_backfill_points(session, test_points, catalog, overwrite_existing_backfill=True)
            await session.commit()
            await session.refresh(rolled_row)
            assert rolled_row.series_origin == SeriesOrigin.LIVE_ROLLUP.value
            assert rolled_row.sell_price_vnd == 136_100_000

        # 3) Test /api/v1/prices/history & error handling
        app = create_app(_make_api_settings())
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                pnj_hist = await client.get(
                    "/api/v1/prices/history",
                    params={"commodity_code": "GOLD_SJC_BAR_PNJ", "location": "HCM", "range": "30d"},
                )
                assert pnj_hist.status_code == 200
                body = pnj_hist.json()
                assert body["timezone"] == VN_TZ_NAME
                assert body["commodity_code"] == "GOLD_SJC_BAR_PNJ"
                assert body["location_code"] == "HCM"
                assert body["applied_zone"] is None
                assert body["stats"]["total_points"] >= 2
                by_d = {pt["date"]: pt for pt in body["points"]}
                assert by_d[d_prev.isoformat()]["sell_price"] == 135_000_000
                assert by_d[d_prev.isoformat()]["origin"] == "BACKFILL"
                assert by_d[d_curr.isoformat()]["sell_price"] == 136_100_000
                assert by_d[d_curr.isoformat()]["origin"] == "LIVE_ROLLUP"

                # FUEL HN must map to applied_zone VUNG_1 seamlessly
                fuel_hist = await client.get(
                    "/api/v1/prices/history",
                    params={"commodity_code": "FUEL_RON95_III", "location": "HN", "range": "30d"},
                )
                assert fuel_hist.status_code == 200
                fbody = fuel_hist.json()
                assert fbody["location_code"] == "HN"
                assert fbody["applied_zone"] == "VUNG_1"
                assert fbody["stats"]["total_points"] >= 2
                f_by_d = {pt["date"]: pt for pt in fbody["points"]}
                assert f_by_d[d_prev.isoformat()]["retail_price"] == 21_000
                assert f_by_d[d_curr.isoformat()]["retail_price"] == 21_500

                missing_resp = await client.get(
                    "/api/v1/prices/history",
                    params={"commodity_code": "NON_EXISTENT", "location": "HCM", "range": "30d"},
                )
                assert missing_resp.status_code == 404
                assert missing_resp.json()["code"] == "COMMODITY_NOT_FOUND"

                invalid_zone_resp = await client.get(
                    "/api/v1/prices/history",
                    params={"commodity_code": "GOLD_SJC_BAR_SJC", "location": "VUNG_1", "range": "30d"},
                )
                assert invalid_zone_resp.status_code == 422
                assert invalid_zone_resp.json()["code"] == "INVALID_LOCATION_FOR_DOMAIN"

                # Verify /prices/latest exposes prev_change_vnd / prev_change_pct cleanly
                latest_resp = await client.get("/api/v1/prices/latest", params={"location": "HCM"})
                assert latest_resp.status_code == 200
                for item in latest_resp.json()["items"]:
                    assert "prev_change_vnd" in item
                    assert "prev_change_pct" in item
    finally:
        async with session_factory() as cleanup:
            for cid, lid, p_date in touched_daily_keys:
                await cleanup.execute(
                    delete(DailyPriceSeries).where(
                        DailyPriceSeries.commodity_id == cid,
                        DailyPriceSeries.location_id == lid,
                        DailyPriceSeries.price_date == p_date,
                    )
                )
            if created_run_ids:
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
                        RawEvidenceSnapshot.content_sha256.in_(created_shas),
                        ~RawEvidenceSnapshot.id.in_(
                            select(CrawlSourceLog.snapshot_id).where(
                                CrawlSourceLog.snapshot_id.is_not(None)
                            )
                        ),
                    )
                )
            for row_dict in backup_daily_rows:
                cleanup.add(DailyPriceSeries(**row_dict))
            await cleanup.commit()
        await engine.dispose()
