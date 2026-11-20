"""Milestone 1 unit tests covering timezone locks, hashing, ORM schema, and catalog seeds."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from vxg_api.main import create_app
from vxg_shared.db.base import Base
from vxg_shared.db.seed import SEED_COMMODITIES, SEED_LOCATIONS, SEED_SOURCES
from vxg_shared.enums import CommodityCategory, PriceUnit, PricingType
from vxg_shared.hashing import compute_content_sha256, compute_price_idempotency_key
from vxg_shared.settings import SharedSettings
from vxg_shared.timezone import VN_TZ, VN_TZ_NAME, format_vn_run_code, to_vn_tz, vn_now, vn_today


def test_timezone_locked_to_asia_ho_chi_minh() -> None:
    now = vn_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=7)
    assert isinstance(vn_today(), date)

    utc_dt = datetime(2026, 11, 19, 17, 30, 0, tzinfo=UTC)
    converted = to_vn_tz(utc_dt)
    assert converted.utcoffset() == timedelta(hours=7)
    # 17:30 UTC is 00:30 next day in UTC+7
    assert converted.day == 20
    assert converted.hour == 0
    assert converted.minute == 30

    with pytest.raises(ValueError, match="Naive datetime"):
        to_vn_tz(datetime(2026, 11, 19, 8, 0, 0))

    run_code = format_vn_run_code("gold", dt=utc_dt, suffix="ab12")
    assert run_code == "RUN-20261120-003000-GOLD-AB12"


def test_content_sha256_and_idempotency_key() -> None:
    assert compute_content_sha256("hello") == compute_content_sha256(b"hello")
    assert len(compute_content_sha256("hello")) == 64

    utc_pub = datetime(2026, 11, 19, 7, 30, 15, 999999, tzinfo=UTC)
    vn_pub = datetime(2026, 11, 19, 14, 30, 15, 0, tzinfo=ZoneInfo(VN_TZ_NAME))

    key_utc = compute_price_idempotency_key(
        commodity_id=1,
        location_id=3,
        source_id=1,
        effective_date=date(2026, 11, 19),
        source_published_at=utc_pub,
        buy_price_vnd=85_000_000,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
    )
    key_vn = compute_price_idempotency_key(
        commodity_id=1,
        location_id=3,
        source_id=1,
        effective_date=date(2026, 11, 19),
        source_published_at=vn_pub,
        buy_price_vnd=85_000_000,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
    )
    assert key_utc == key_vn
    assert len(key_utc) == 64

    key_changed_price = compute_price_idempotency_key(
        commodity_id=1,
        location_id=3,
        source_id=1,
        effective_date=date(2026, 11, 19),
        source_published_at=vn_pub,
        buy_price_vnd=85_100_000,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
    )
    assert key_changed_price != key_vn


def test_settings_database_url_normalization() -> None:
    s1 = SharedSettings.model_validate(
        {"DATABASE_URL": "postgres://user:pass@host:5432/db", "DATABASE_URL_SYNC": None}
    )
    assert s1.async_database_url == "postgresql+asyncpg://user:pass@host:5432/db"
    assert s1.sync_database_url == "postgresql+psycopg://user:pass@host:5432/db"

    s2 = SharedSettings.model_validate(
        {"DATABASE_URL": "postgresql://user:pass@host:5432/db", "DATABASE_URL_SYNC": None}
    )
    assert s2.async_database_url == "postgresql+asyncpg://user:pass@host:5432/db"
    assert s2.sync_database_url == "postgresql+psycopg://user:pass@host:5432/db"

    s3 = SharedSettings.model_validate(
        {
            "DATABASE_URL": "postgresql://user:pass@host:5432/db",
            "DATABASE_URL_SYNC": "postgres://sync_user:sync_pass@host:5432/sync_db",
        }
    )
    assert s3.sync_database_url == "postgresql+psycopg://sync_user:sync_pass@host:5432/sync_db"


def test_orm_metadata_contains_all_tables_and_indexes() -> None:
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
        "forecast_runs",
    }
    assert set(Base.metadata.tables.keys()) == expected_tables

    all_index_names = {
        idx.name for table in Base.metadata.tables.values() for idx in table.indexes
    }
    expected_indexes = {
        "idx_crawl_runs_created_desc",
        "idx_crawl_source_logs_run_id",
        "idx_crawl_source_logs_source_health",
        "idx_price_records_latest_verified",
        "idx_price_records_timeseries",
        "idx_price_records_crawl_run",
        "idx_anomaly_alerts_open",
        "idx_daily_price_series_lookup",
        "idx_daily_price_series_adj",
        "idx_macro_indicators_code_date_desc",
        "idx_market_news_domain_pub_desc",
        "idx_market_news_impact_pub_desc",
        "idx_price_forecasts_latest",
        "idx_price_forecasts_pending_eval",
        "idx_forecast_evaluations_target_desc",
        "idx_forecast_evaluations_group",
        "idx_forecast_evaluations_type_target",
        "idx_forecast_runs_created_desc",
    }
    assert expected_indexes.issubset(all_index_names)


def test_render_free_packages_exclude_ml_dependencies() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for pyproject_rel in (
        "packages/shared-py/pyproject.toml",
        "apps/api/pyproject.toml",
    ):
        text = (repo_root / pyproject_rel).read_text(encoding="utf-8").lower()
        assert "numpy" not in text
        assert "scikit-learn" not in text
        assert "sklearn" not in text


def test_catalog_seed_integrity_no_fake_prices() -> None:
    location_codes = [loc["code"] for loc in SEED_LOCATIONS]
    assert len(location_codes) == len(set(location_codes))
    assert {"HN", "DN", "HCM", "VUNG_1", "VUNG_2"} == set(location_codes)

    for city in [loc for loc in SEED_LOCATIONS if loc["location_type"] == "CITY"]:
        assert city["fuel_zone_code"] == "VUNG_1"

    commodity_codes = [c["code"] for c in SEED_COMMODITIES]
    assert len(commodity_codes) == len(set(commodity_codes))
    categories = {c["category"] for c in SEED_COMMODITIES}
    assert categories == {
        CommodityCategory.GOLD.value,
        CommodityCategory.FUEL.value,
        CommodityCategory.GAS.value,
    }
    for c in SEED_COMMODITIES:
        if c["category"] == CommodityCategory.GOLD.value:
            assert c["unit"] == PriceUnit.VND_PER_LUONG.value
            assert c["pricing_type"] == PricingType.BUY_SELL.value
        elif c["category"] == CommodityCategory.FUEL.value:
            assert c["unit"] == PriceUnit.VND_PER_LITER.value
            assert c["pricing_type"] == PricingType.SINGLE_RETAIL.value
        elif c["category"] == CommodityCategory.GAS.value:
            assert c["unit"] == PriceUnit.VND_PER_CYLINDER.value
            assert c["pricing_type"] == PricingType.SINGLE_RETAIL.value

    source_codes = [s["code"] for s in SEED_SOURCES]
    assert len(source_codes) == len(set(source_codes))
    active_sources = [s for s in SEED_SOURCES if s["is_active"]]
    assert len(active_sources) == 11
    assert {s["category"] for s in active_sources} == categories
    for src in SEED_SOURCES:
        assert src["base_url"].startswith("https://")
        assert src["endpoint_url"].startswith("https://")


def test_api_health_endpoint_returns_vn_timezone() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["timezone"] == VN_TZ_NAME
    parsed_dt = datetime.fromisoformat(body["server_time_vn"])
    assert parsed_dt.tzinfo is not None
    assert parsed_dt.utcoffset() == VN_TZ.utcoffset(parsed_dt)

