"""Integration tests for Milestone 4 FastAPI endpoints against live PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import delete, func, select, update

from vxg_api.config import ApiSettings
from vxg_api.main import create_app
from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import (
    AnomalyAlert,
    Commodity,
    CrawlRun,
    CrawlSourceLog,
    DailyPriceSeries,
    Location,
    PriceRecord,
    RawEvidenceSnapshot,
    Source,
)
from vxg_shared.enums import (
    CrawlRunStatus,
    CrawlScope,
    FetchStatus,
    TriggerType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_content_sha256, compute_price_idempotency_key
from vxg_shared.timezone import VN_TZ_NAME, vn_now, vn_today
from vxg_worker.config import WorkerSettings
from vxg_worker.fetcher.two_tier import TwoTierFetcher
from vxg_worker.fetcher.types import FetcherTier, FetchResult
from vxg_worker.locks import PostgresAdvisoryLock
from vxg_worker.runner import CrawlRunner

_HOST_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker",
)
_TEST_ADMIN_KEY = "test-milestone4-admin-key-secret"
_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

_FIXTURES_BY_SOURCE_CODE: dict[str, tuple[str, str]] = {
    "SJC_OFFICIAL_XML": ("gold/sjc_gold.json", "text/json"),
    "DOJI_BOARD": ("gold/doji_board.html", "text/html; charset=utf-8"),
    "PNJ_HTML": ("gold/pnj_gold.html", "text/html; charset=utf-8"),
    "CAFEF_GOLD_CROSSCHECK": ("gold/cafef_gold.json", "application/json"),
    "PETROLIMEX_FUEL": ("fuel/petrolimex_fuel.html", "text/html; charset=utf-8"),
    "SAIGONPETRO_FUEL": ("fuel/saigonpetro_fuel_list.html", "text/html; charset=utf-8"),
    "WEBGIA_FUEL_CROSSCHECK": ("fuel/webgia_fuel.html", "text/html; charset=utf-8"),
    "PETROLIMEX_GAS": ("gas/petrolimex_gas.html", "text/html; charset=utf-8"),
    "SAIGONPETRO_GAS": ("gas/saigonpetro_gas_list.html", "text/html; charset=utf-8"),
    "PVGAS_LPG": ("gas/pvgas_lpg.html", "text/html; charset=utf-8"),
    "VIETNAMBIZ_GAS_CROSSCHECK": ("gas/vietnambiz_gas_list.html", "text/html; charset=utf-8"),
}


class _GoldenFixtureFetcher(TwoTierFetcher):
    """Offline fetcher serving saved real Golden Fixtures when CI DB starts empty."""

    def __init__(self) -> None:
        super().__init__()
        self.used_shas: set[str] = set()

    def _build_result(self, url: str, rel_path: str, content_type: str) -> FetchResult:
        raw = (_FIXTURES_DIR / rel_path).read_bytes()
        sha = compute_content_sha256(raw)
        self.used_shas.add(sha)
        return FetchResult(
            request_url=url,
            final_url=url,
            http_status=200,
            content_type=content_type,
            raw_bytes=raw,
            content_sha256=sha,
            byte_size=len(raw),
            response_time_ms=25,
            attempts=1,
            tier_used=FetcherTier.HTTPX,
            fetch_status=FetchStatus.SUCCESS,
        )

    async def fetch_source(self, source: Source) -> FetchResult:
        rel_path, content_type = _FIXTURES_BY_SOURCE_CODE[source.code]
        return self._build_result(source.endpoint_url, rel_path, content_type)

    async def fetch_url(
        self,
        url: str,
        *,
        fetch_strategy: str = "HTTP_HTML",
        timeout_ms: int = 15_000,
        rate_limit_delay_ms: int = 0,
        referer: str | None = None,
    ) -> FetchResult:
        _ = (fetch_strategy, timeout_ms, rate_limit_delay_ms)
        ref = (referer or "").lower()
        lowered = url.lower()
        if "xang-dau" in ref or "xang-dau" in lowered:
            rel_path = "fuel/saigonpetro_fuel_detail.html"
        elif "vietnambiz.vn" in lowered or "vietnambiz.vn" in ref:
            rel_path = "gas/vietnambiz_gas_detail.html"
        else:
            rel_path = "gas/saigonpetro_gas_detail.html"
        return self._build_result(url, rel_path, "text/html; charset=utf-8")


async def _bootstrap_ci_data_if_empty() -> tuple[uuid.UUID | None, list[str], dict[int, tuple[Any, int]]]:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            price_count = int(
                (await session.execute(select(func.count(PriceRecord.id)))).scalar_one()
            )
            completed_all = (
                await session.execute(
                    select(CrawlRun.id)
                    .where(
                        CrawlRun.scope_category == CrawlScope.ALL.value,
                        CrawlRun.status == CrawlRunStatus.COMPLETED.value,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if price_count > 0 and completed_all is not None:
                return None, [], {}

            sources = list((await session.execute(select(Source))).scalars().all())
            original_source_state: dict[int, tuple[Any, int]] = {
                s.id: (s.last_success_at, s.consecutive_failures) for s in sources
            }

        fetcher = _GoldenFixtureFetcher()
        settings = WorkerSettings.model_validate(
            {
                "DATABASE_URL": _HOST_TEST_DB_URL,
                "DATABASE_URL_SYNC": None,
                "advisory_lock_key": 908_712_997,
            }
        )
        runner = CrawlRunner(
            engine=engine,
            session_factory=session_factory,
            settings=settings,
            fetcher=fetcher,
        )
        summary = await runner.execute_run(
            scope=CrawlScope.ALL,
            trigger_type=TriggerType.MANUAL_ADMIN,
        )
        assert summary.status == CrawlRunStatus.COMPLETED.value
        assert summary.pages_fetched_success == 11
        return summary.run_id, sorted(fetcher.used_shas), original_source_state
    finally:
        await engine.dispose()


async def _cleanup_ci_bootstrap_data(
    run_id: uuid.UUID,
    used_shas: list[str],
    original_source_state: dict[int, tuple[Any, int]],
) -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as cleanup:
            await cleanup.execute(delete(AnomalyAlert).where(AnomalyAlert.crawl_run_id == run_id))
            await cleanup.execute(
                delete(DailyPriceSeries).where(
                    DailyPriceSeries.source_record_id.in_(
                        select(PriceRecord.id).where(PriceRecord.crawl_run_id == run_id)
                    )
                )
            )
            await cleanup.execute(delete(PriceRecord).where(PriceRecord.crawl_run_id == run_id))
            await cleanup.execute(
                delete(CrawlSourceLog).where(CrawlSourceLog.crawl_run_id == run_id)
            )
            await cleanup.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
            if used_shas:
                await cleanup.execute(
                    delete(RawEvidenceSnapshot).where(
                        RawEvidenceSnapshot.content_sha256.in_(used_shas)
                    )
                )
            for src_id, (prev_last, prev_fail) in original_source_state.items():
                await cleanup.execute(
                    update(Source)
                    .where(Source.id == src_id)
                    .values(last_success_at=prev_last, consecutive_failures=prev_fail)
                )
            await cleanup.commit()
    finally:
        await engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def ensure_api_test_data() -> Iterator[None]:
    """Use existing live PostgreSQL rows locally, or seed offline via Golden Fixtures in CI."""
    seeded_run_id, used_shas, prev_sources = asyncio.run(_bootstrap_ci_data_if_empty())
    try:
        yield
    finally:
        if seeded_run_id is not None:
            asyncio.run(_cleanup_ci_bootstrap_data(seeded_run_id, used_shas, prev_sources))


def _make_test_settings() -> ApiSettings:
    return ApiSettings.model_validate(
        {
            "DATABASE_URL": _HOST_TEST_DB_URL,
            "DATABASE_URL_SYNC": None,
            "CORS_ORIGINS": "http://localhost:3000",
            "ADMIN_API_KEY": _TEST_ADMIN_KEY,
        }
    )


@asynccontextmanager
async def _api_test_client() -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    app = create_app(_make_test_settings())
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, app


@pytest.mark.asyncio
async def test_catalog_endpoint() -> None:
    async with _api_test_client() as (client, _app):
        resp = await client.get("/api/v1/catalog")

    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == VN_TZ_NAME
    assert body["server_time_vn"].endswith("+07:00")
    assert [c["code"] for c in body["cities"]] == ["HN", "DN", "HCM"]
    assert [d["code"] for d in body["domains"]] == ["GOLD", "FUEL", "GAS"]
    assert len(body["commodities"]) == 18
    assert len(body["sources"]) == 11
    assert all(src["is_active"] is True for src in body["sources"])


@pytest.mark.asyncio
async def test_latest_prices_dn_fuel_zone_mapping_and_distinct_sources() -> None:
    async with _api_test_client() as (client, _app):
        dn_resp = await client.get("/api/v1/prices/latest", params={"location": "DN"})
        gold_resp = await client.get("/api/v1/prices/latest", params={"domain": "GOLD"})
        bad_resp = await client.get("/api/v1/prices/latest", params={"location": "INVALID"})

    assert dn_resp.status_code == 200
    dn_body = dn_resp.json()
    assert dn_body["timezone"] == VN_TZ_NAME
    assert dn_body["as_of_vn"].endswith("+07:00")
    assert dn_body["location_filter"] == "DN"
    assert dn_body["fuel_zone_mapping"] == {"DN": "VUNG_1"}
    assert dn_body["count"] == len(dn_body["items"])
    assert dn_body["count"] > 0
    assert dn_body["last_updated_vn"].endswith("+07:00")

    fuel_items = [item for item in dn_body["items"] if item["domain"] == "FUEL"]
    assert len(fuel_items) >= 8
    for item in fuel_items:
        assert item["location_code"] == "DN"
        assert item["applied_zone"] == "VUNG_1"
        assert item["retail_price"] > 0

    ron95_dn = [item for item in fuel_items if item["commodity_code"] == "FUEL_RON95_III"]
    ron95_by_source = {item["source_code"]: item for item in ron95_dn}
    assert {"PETROLIMEX_FUEL", "SAIGONPETRO_FUEL"}.issubset(ron95_by_source.keys())
    assert ron95_by_source["PETROLIMEX_FUEL"]["brand"] == "PETROLIMEX"
    assert ron95_by_source["SAIGONPETRO_FUEL"]["brand"] == "SAIGON_PETRO"

    non_fuel = [item for item in dn_body["items"] if item["domain"] in ("GOLD", "GAS")]
    assert len(non_fuel) > 0
    for item in non_fuel:
        assert item["location_code"] == "DN"
        assert item["applied_zone"] is None

    for item in dn_body["items"]:
        assert item["verification_status"] in ("VERIFIED", "CROSS_CHECKED")
        assert item["fetched_at_vn"].endswith("+07:00")
        assert item["last_verified_at_vn"].endswith("+07:00")
        if item["published_at_vn"] is not None:
            assert item["published_at_vn"].endswith("+07:00")

    assert gold_resp.status_code == 200
    gold_body = gold_resp.json()
    assert gold_body["domain_filter"] == "GOLD"
    assert gold_body["location_filter"] is None
    assert {item["location_code"] for item in gold_body["items"]} == {"HN", "DN", "HCM"}

    assert bad_resp.status_code == 422
    bad_body = bad_resp.json()
    assert bad_body["code"] == "VALIDATION_ERROR"
    assert "details" in bad_body


@pytest.mark.asyncio
async def test_flagged_price_never_leaks_to_latest_prices() -> None:
    synthetic_record_id: uuid.UUID | None = None
    async with _api_test_client() as (client, app):
        session_factory = app.state.session_factory  # type: ignore[attr-defined]
        async with session_factory() as session:
            sjc_comm_id = (
                await session.execute(select(Commodity.id).where(Commodity.code == "GOLD_SJC_BAR_SJC"))
            ).scalar_one()
            dn_loc_id = (
                await session.execute(select(Location.id).where(Location.code == "DN"))
            ).scalar_one()
            sjc_source_id = (
                await session.execute(select(Source.id).where(Source.code == "SJC_OFFICIAL_XML"))
            ).scalar_one()
            latest_log = (
                await session.execute(
                    select(CrawlSourceLog)
                    .where(CrawlSourceLog.source_id == sjc_source_id)
                    .order_by(CrawlSourceLog.created_at.desc())
                    .limit(1)
                )
            ).scalar_one()

            future_ts = vn_now() + timedelta(minutes=5)
            eff_date = vn_today()
            synth_buy = 250_000_000
            synth_sell = 255_000_000
            synthetic = PriceRecord(
                commodity_id=sjc_comm_id,
                location_id=dn_loc_id,
                source_id=sjc_source_id,
                crawl_run_id=latest_log.crawl_run_id,
                source_log_id=latest_log.id,
                buy_price_vnd=synth_buy,
                sell_price_vnd=synth_sell,
                retail_price_vnd=None,
                effective_date=eff_date,
                source_published_at=future_ts,
                crawled_at=future_ts,
                last_verified_at=future_ts,
                source_url="https://sjc.com.vn/test-synthetic-flagged",
                evidence_sha256="0" * 64,
                verification_status=VerificationStatus.FLAGGED.value,
                validation_notes={"reason": "synthetic quarantine test"},
                idempotency_key=compute_price_idempotency_key(
                    commodity_id=sjc_comm_id,
                    location_id=dn_loc_id,
                    source_id=sjc_source_id,
                    effective_date=eff_date,
                    source_published_at=future_ts,
                    buy_price_vnd=synth_buy,
                    sell_price_vnd=synth_sell,
                    retail_price_vnd=None,
                ),
            )
            session.add(synthetic)
            await session.commit()
            await session.refresh(synthetic)
            synthetic_record_id = synthetic.id

        try:
            resp = await client.get(
                "/api/v1/prices/latest",
                params={"location": "DN", "domain": "GOLD"},
            )
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert all(item["record_id"] != str(synthetic_record_id) for item in items)
            assert all(item["buy_price"] != 250_000_000 for item in items)
            assert any(item["commodity_code"] == "GOLD_SJC_BAR_SJC" for item in items)
        finally:
            if synthetic_record_id is not None:
                async with session_factory() as cleanup:
                    await cleanup.execute(
                        delete(PriceRecord).where(PriceRecord.id == synthetic_record_id)
                    )
                    await cleanup.commit()


@pytest.mark.asyncio
async def test_price_evidence_endpoint_and_404() -> None:
    async with _api_test_client() as (client, _app):
        latest_resp = await client.get(
            "/api/v1/prices/latest",
            params={"location": "DN", "domain": "GOLD"},
        )
        assert latest_resp.status_code == 200
        cross_checked_item = next(
            item
            for item in latest_resp.json()["items"]
            if item["verification_status"] == "CROSS_CHECKED"
        )
        record_id = cross_checked_item["record_id"]

        ev_resp = await client.get(f"/api/v1/prices/{record_id}/evidence")
        missing_resp = await client.get(f"/api/v1/prices/{uuid.uuid4()}/evidence")

    assert ev_resp.status_code == 200
    ev = ev_resp.json()
    assert ev["record_id"] == record_id
    assert ev["run_code"].startswith("RUN-")
    assert len(ev["content_sha256"]) == 64
    assert ev["source_url"].startswith("http")
    assert ev["source_name"]
    assert ev["cross_check_delta_pct"] is not None
    assert len(ev["cross_check_sources"]) == 1
    assert ev["fetched_at_vn"].endswith("+07:00")
    assert ev["last_verified_at_vn"].endswith("+07:00")

    assert missing_resp.status_code == 404
    assert missing_resp.json()["code"] == "PRICE_RECORD_NOT_FOUND"


@pytest.mark.asyncio
async def test_audit_summary_runs_detail_sources_health_and_anomalies() -> None:
    async with _api_test_client() as (client, _app):
        summary_resp = await client.get("/api/v1/audit/summary")
        runs_resp = await client.get("/api/v1/audit/runs", params={"limit": 5, "offset": 0})
        completed_all_resp = await client.get(
            "/api/v1/audit/runs",
            params={"scope": "ALL", "status": "COMPLETED", "limit": 5, "offset": 0},
        )
        health_resp = await client.get("/api/v1/sources/health")
        open_anom_resp = await client.get("/api/v1/audit/anomalies", params={"status": "OPEN"})
        res_anom_resp = await client.get("/api/v1/audit/anomalies", params={"status": "RESOLVED"})
        missing_run_resp = await client.get(f"/api/v1/audit/runs/{uuid.uuid4()}")

        assert runs_resp.status_code == 200
        runs_body = runs_resp.json()
        assert completed_all_resp.status_code == 200
        completed_all_items = completed_all_resp.json()["items"]
        assert len(completed_all_items) >= 1
        completed_run = completed_all_items[0]
        run_detail_resp = await client.get(f"/api/v1/audit/runs/{completed_run['id']}")

    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["timezone"] == VN_TZ_NAME
    assert summary["as_of_vn"].endswith("+07:00")
    assert summary["active_sources_count"] == 11
    assert summary["open_anomalies"] >= 0
    assert summary["today"]["total_runs"] >= 1
    assert summary["last_7_days"]["sources_queried"] >= 11
    assert summary["last_7_days"]["pages_fetched_ok"] >= 11
    assert summary["last_7_days"]["documents_parsed"] >= 11
    assert summary["last_7_days"]["records_verified"] >= 46

    assert runs_body["total"] >= 1
    assert runs_body["limit"] == 5

    assert run_detail_resp.status_code == 200
    detail = run_detail_resp.json()
    assert detail["id"] == completed_run["id"]
    assert len(detail["source_logs"]) == 11
    for log in detail["source_logs"]:
        assert log["request_url"].startswith("http")
        assert log["http_status"] == 200
        assert log["tier_used"] in ("HTTPX", "PLAYWRIGHT")
        assert log["latency_ms"] >= 0
        assert log["bytes_received"] > 0
        assert len(log["content_sha256"]) == 64
        assert log["created_at_vn"].endswith("+07:00")

    assert missing_run_resp.status_code == 404
    assert missing_run_resp.json()["code"] == "CRAWL_RUN_NOT_FOUND"

    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["total_active_sources"] == 11
    assert len(health["items"]) == 11
    for item in health["items"]:
        assert item["success_rate_7d_pct"] is not None
        assert item["avg_latency_ms_7d"] is not None
        assert item["last_success_at_vn"].endswith("+07:00")
        assert item["current_status"] in ("HEALTHY", "DEGRADED", "DOWN", "UNKNOWN")

    assert open_anom_resp.status_code == 200
    assert res_anom_resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_crawl_runs_auth_conflict_and_202() -> None:
    created_run_id: uuid.UUID | None = None
    async with _api_test_client() as (client, app):
        engine = app.state.engine  # type: ignore[attr-defined]
        session_factory = app.state.session_factory  # type: ignore[attr-defined]

        no_key = await client.post("/api/v1/admin/crawl-runs", json={"scope": "GOLD"})
        assert no_key.status_code == 401
        assert no_key.json()["code"] == "ADMIN_API_KEY_MISSING"

        bad_key = await client.post(
            "/api/v1/admin/crawl-runs",
            headers={"X-Admin-API-Key": "wrong-secret"},
            json={"scope": "GOLD"},
        )
        assert bad_key.status_code == 403
        assert bad_key.json()["code"] == "ADMIN_API_KEY_INVALID"

        # Hold worker advisory lock briefly so live vxg_worker defers pickup until cleanup.
        async with PostgresAdvisoryLock(engine, 908_712_001) as acquired:
            assert acquired is True
            try:
                queued_resp = await client.post(
                    "/api/v1/admin/crawl-runs",
                    headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
                    json={"scope": "GOLD"},
                )
                assert queued_resp.status_code == 202
                queued_body = queued_resp.json()
                created_run_id = uuid.UUID(queued_body["run_id"])
                assert queued_body["run_code"].startswith("RUN-")
                assert "-GOLD-" in queued_body["run_code"]
                assert queued_body["scope"] == "GOLD"
                assert queued_body["status"] == "QUEUED"
                assert queued_body["queued_at_vn"].endswith("+07:00")

                conflict_same = await client.post(
                    "/api/v1/admin/crawl-runs",
                    headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
                    json={"scope": "GOLD"},
                )
                assert conflict_same.status_code == 409
                conflict_body = conflict_same.json()
                assert conflict_body["code"] == "CRAWL_RUN_ALREADY_ACTIVE"
                assert conflict_body["details"]["active_run_id"] == queued_body["run_id"]

                conflict_all = await client.post(
                    "/api/v1/admin/crawl-runs",
                    headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
                    json={"scope": "ALL"},
                )
                assert conflict_all.status_code == 409
                assert conflict_all.json()["code"] == "CRAWL_RUN_ALREADY_ACTIVE"
            finally:
                if created_run_id is not None:
                    async with session_factory() as cleanup:
                        await cleanup.execute(delete(CrawlRun).where(CrawlRun.id == created_run_id))
                        await cleanup.commit()


@pytest.mark.asyncio
async def test_openapi_and_cors_headers() -> None:
    async with _api_test_client() as (client, _app):
        spec_resp = await client.get("/openapi.json")
        docs_resp = await client.get("/docs")
        cors_resp = await client.options(
            "/api/v1/catalog",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert spec_resp.status_code == 200
    assert docs_resp.status_code == 200
    spec = spec_resp.json()
    paths = set(spec["paths"].keys())
    expected_paths = {
        "/health",
        "/ready",
        "/api/v1/catalog",
        "/api/v1/prices/latest",
        "/api/v1/prices/{record_id}/evidence",
        "/api/v1/audit/summary",
        "/api/v1/audit/runs",
        "/api/v1/audit/runs/{run_id}",
        "/api/v1/sources/health",
        "/api/v1/audit/anomalies",
        "/api/v1/admin/crawl-runs",
    }
    assert expected_paths.issubset(paths)
    phase2_paths = {
        "/api/v1/prices/history",
        "/api/v1/analytics/macro",
        "/api/v1/analytics/news",
        "/api/v1/forecasts/latest",
        "/api/v1/forecasts/detail",
        "/api/v1/audit/forecasts",
        "/api/v1/admin/forecast-runs",
    }
    assert phase2_paths.issubset(paths)
    assert "APIKeyHeader" in spec["components"]["securitySchemes"]

    assert cors_resp.status_code == 200
    assert cors_resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
