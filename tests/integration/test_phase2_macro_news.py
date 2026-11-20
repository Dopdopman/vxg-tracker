"""PostgreSQL integration tests for Milestone 8 macro sync, news sync & analytics APIs."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from vxg_api.config import ApiSettings
from vxg_api.main import create_app
from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import DailyPriceSeries, MacroIndicator, MarketNewsSignal
from vxg_shared.enums import MacroIndicatorCode, SeriesOrigin
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.config import WorkerSettings
from vxg_worker.macro.sync import run_macro_sync
from vxg_worker.news.rss_fetcher import RssFeedSpec
from vxg_worker.news.sync import run_news_sync
from vxg_worker.persistence import load_catalog_lookup

_HOST_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker",
)
_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _make_api_settings() -> ApiSettings:
    return ApiSettings.model_validate(
        {
            "DATABASE_URL": _HOST_TEST_DB_URL,
            "DATABASE_URL_SYNC": None,
            "CORS_ORIGINS": "http://localhost:3000",
            "ADMIN_API_KEY": "test-milestone8-admin-key-secret",
        }
    )


def _make_yahoo_payload(symbol: str, tz_name: str, dates_and_values: list[tuple[date, float]]) -> bytes:
    timestamps = [
        int(datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC).timestamp())
        for d, _ in dates_and_values
    ]
    closes = [val for _, val in dates_and_values]
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD" if symbol != "VND=X" else "VND",
                        "symbol": symbol,
                        "exchangeTimezoneName": tz_name,
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": closes,
                                "low": closes,
                                "close": closes,
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }
    return json.dumps(payload).encode("utf-8")


@pytest.mark.asyncio
async def test_macro_constraint_sync_idempotency_and_macro_analytics_api() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    today = vn_today()
    test_dates = [today - timedelta(days=delta) for delta in range(10, -1, -1)]
    d_vcb_only_gap = today - timedelta(days=1)
    d_vcb_present = today - timedelta(days=2)

    backup_macro: list[dict[str, Any]] = []
    backup_sjc: list[dict[str, Any]] = []

    try:
        async with session_factory() as session:
            # Verify Migration 0003 allows USD_VND_MARKET and blocks unknown indicator codes
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "INSERT INTO macro_indicators "
                            "(indicator_code, indicator_date, value, unit, source_code, source_url) "
                            "VALUES ('BOGUS_CODE', '2026-11-20', 100.0, 'UNIT', 'TEST', 'https://example.com')"
                        )
                    )

            catalog = await load_catalog_lookup(session)
            sjc_bar = catalog.commodities_by_code["GOLD_SJC_BAR_SJC"]
            hcm = catalog.locations_by_code["HCM"]

            # Backup pre-existing macro rows & SJC daily rows on test window so live DB is preserved
            existing_macro = (
                await session.execute(
                    select(MacroIndicator).where(MacroIndicator.indicator_date.in_(test_dates))
                )
            ).scalars().all()
            for r in existing_macro:
                backup_macro.append(
                    {
                        "indicator_code": r.indicator_code,
                        "indicator_date": r.indicator_date,
                        "value": r.value,
                        "open_value": r.open_value,
                        "high_value": r.high_value,
                        "low_value": r.low_value,
                        "change_pct_1d": r.change_pct_1d,
                        "unit": r.unit,
                        "source_code": r.source_code,
                        "source_url": r.source_url,
                        "content_sha256": r.content_sha256,
                        "metadata_json": dict(r.metadata_json),
                        "fetched_at": r.fetched_at,
                    }
                )

            existing_sjc = (
                await session.execute(
                    select(DailyPriceSeries).where(
                        DailyPriceSeries.commodity_id == sjc_bar.id,
                        DailyPriceSeries.location_id == hcm.id,
                        DailyPriceSeries.price_date.in_([d_vcb_present, d_vcb_only_gap, today]),
                    )
                )
            ).scalars().all()
            for s_row in existing_sjc:
                backup_sjc.append(
                    {
                        "commodity_id": s_row.commodity_id,
                        "location_id": s_row.location_id,
                        "price_date": s_row.price_date,
                        "buy_price_vnd": s_row.buy_price_vnd,
                        "sell_price_vnd": s_row.sell_price_vnd,
                        "retail_price_vnd": s_row.retail_price_vnd,
                        "reference_price_vnd": s_row.reference_price_vnd,
                        "series_origin": s_row.series_origin,
                        "is_adjustment_day": s_row.is_adjustment_day,
                        "source_record_id": s_row.source_record_id,
                        "source_code": s_row.source_code,
                        "source_url": s_row.source_url,
                        "evidence_sha256": s_row.evidence_sha256,
                        "metadata_json": dict(s_row.metadata_json),
                        "updated_at": s_row.updated_at,
                    }
                )

            # Ensure clean slate on d_vcb_present..today for exact deterministic assertions
            await session.execute(
                delete(MacroIndicator).where(MacroIndicator.indicator_date.in_(test_dates))
            )
            await session.execute(
                delete(DailyPriceSeries).where(
                    DailyPriceSeries.commodity_id == sjc_bar.id,
                    DailyPriceSeries.location_id == hcm.id,
                    DailyPriceSeries.price_date.in_([d_vcb_present, d_vcb_only_gap, today]),
                )
            )
            await session.commit()

        # Build mock transport returning deterministic Yahoo series & VCB JSON for all days EXCEPT d_vcb_only_gap & today
        xau_points = [(d, 3800.0 + idx * 10.0) for idx, d in enumerate(test_dates)]
        brent_points = [(d, 72.0 + idx * 0.5) for idx, d in enumerate(test_dates)]
        wti_points = [(d, 68.0 + idx * 0.4) for idx, d in enumerate(test_dates)]
        dxy_points = [(d, 105.0 - idx * 0.2) for idx, d in enumerate(test_dates)]
        vnd_market_points = [(d, 25500.0) for d in test_dates]

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "GC%3DF" in url_str or "GC=F" in url_str:
                return httpx.Response(200, content=_make_yahoo_payload("GC=F", "UTC", xau_points))
            if "BZ%3DF" in url_str or "BZ=F" in url_str:
                return httpx.Response(200, content=_make_yahoo_payload("BZ=F", "UTC", brent_points))
            if "CL%3DF" in url_str or "CL=F" in url_str:
                return httpx.Response(200, content=_make_yahoo_payload("CL=F", "UTC", wti_points))
            if "DX-Y.NYB" in url_str:
                return httpx.Response(200, content=_make_yahoo_payload("DX-Y.NYB", "UTC", dxy_points))
            if "VND%3DX" in url_str or "VND=X" in url_str:
                return httpx.Response(
                    200, content=_make_yahoo_payload("VND=X", "UTC", vnd_market_points)
                )
            if "vietcombank.com.vn/api/exchangerates" in url_str:
                req_date_str = request.url.params.get("date", today.isoformat())
                if req_date_str in (d_vcb_only_gap.isoformat(), today.isoformat()):
                    return httpx.Response(200, json={"Count": 0, "Data": []})
                return httpx.Response(
                    200,
                    json={
                        "Count": 1,
                        "Date": f"{req_date_str}T00:00:00",
                        "Data": [
                            {
                                "currencyCode": "USD",
                                "cash": "25550.00",
                                "transfer": "25580.00",
                                "sell": "25960.00",
                            }
                        ],
                    },
                )
            if "pXML.aspx" in url_str:
                return httpx.Response(404)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            summary1 = await run_macro_sync(
                engine,
                session_factory,
                mode="sync",
                sync_days=10,
                advisory_lock_key=908_712_911,
                http_client=mock_client,
                delay_ms=0,
                vcb_delay_ms=0,
                ref_today=today,
            )
            summary2 = await run_macro_sync(
                engine,
                session_factory,
                mode="sync",
                sync_days=10,
                advisory_lock_key=908_712_911,
                http_client=mock_client,
                delay_ms=0,
                vcb_delay_ms=0,
                ref_today=today,
            )

        assert summary1.errors == {}
        assert summary2.errors == {}
        assert summary1.upserted_by_indicator["XAU_USD"] == 11
        assert summary1.upserted_by_indicator["USD_VND_MARKET"] == 11
        assert summary1.upserted_by_indicator["USD_VND_VCB"] == 8

        async with session_factory() as session:
            count_in_window = int(
                (
                    await session.execute(
                        select(MacroIndicator)
                        .where(MacroIndicator.indicator_date.in_(test_dates))
                    )
                )
                .scalars()
                .all()
                .__len__()
            )
            assert count_in_window == (11 * 5 + 8)

            # Insert synthetic SJC bar prices on d_vcb_present (VCB exists) and d_vcb_only_gap (VCB missing, fallback USD_VND_MARKET only if no VCB within 0..5? No: select_fx_rate_for_date prefers exact-date market over stale VCB!)
            catalog = await load_catalog_lookup(session)
            sjc_bar = catalog.commodities_by_code["GOLD_SJC_BAR_SJC"]
            hcm = catalog.locations_by_code["HCM"]
            session.add_all(
                [
                    DailyPriceSeries(
                        commodity_id=sjc_bar.id,
                        location_id=hcm.id,
                        price_date=d_vcb_present,
                        buy_price_vnd=132_000_000,
                        sell_price_vnd=135_000_000,
                        retail_price_vnd=None,
                        reference_price_vnd=135_000_000,
                        series_origin=SeriesOrigin.BACKFILL.value,
                        is_adjustment_day=True,
                        source_code="TEST_M8",
                        source_url="https://example.test/sjc",
                    ),
                    DailyPriceSeries(
                        commodity_id=sjc_bar.id,
                        location_id=hcm.id,
                        price_date=d_vcb_only_gap,
                        buy_price_vnd=133_000_000,
                        sell_price_vnd=136_000_000,
                        retail_price_vnd=None,
                        reference_price_vnd=136_000_000,
                        series_origin=SeriesOrigin.LIVE_ROLLUP.value,
                        is_adjustment_day=True,
                        source_code="TEST_M8",
                        source_url="https://example.test/sjc",
                    ),
                ]
            )
            await session.commit()

        app = create_app(_make_api_settings())
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/api/v1/analytics/macro?range=30d")
            assert resp.status_code == 200
            data = resp.json()
            assert set(data["latest"].keys()) == {c.value for c in MacroIndicatorCode}
            assert data["latest"]["XAU_USD"]["change_pct_7d"] is not None
            assert data["sjc_world_spread"] is not None
            # On d_vcb_only_gap VCB was absent while USD_VND_MARKET matched same-day
            assert data["sjc_world_spread"]["sjc_date"] == d_vcb_only_gap.isoformat()
            assert data["sjc_world_spread"]["fx_source"] == "USD_VND_MARKET"
            assert "37,5g" in data["sjc_world_spread"]["disclaimer_vi"]

            spread_by_date = {p["date"]: p for p in data["sjc_world_spread_series"]}
            assert spread_by_date[d_vcb_present.isoformat()]["fx_source"] == "USD_VND_VCB"

            filtered = await client.get("/api/v1/analytics/macro?indicators=XAU_USD,BRENT_USD")
            assert filtered.status_code == 200
            assert set(filtered.json()["series"].keys()) == {"XAU_USD", "BRENT_USD"}

            bad = await client.get("/api/v1/analytics/macro?indicators=UNKNOWN_MACRO")
            assert bad.status_code == 422
            assert bad.json()["code"] == "INVALID_MACRO_INDICATOR"
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(MacroIndicator).where(MacroIndicator.indicator_date.in_(test_dates))
            )
            if backup_macro:
                await cleanup_session.execute(pg_insert(MacroIndicator).values(backup_macro))

            catalog = await load_catalog_lookup(cleanup_session)
            sjc_id = catalog.commodities_by_code["GOLD_SJC_BAR_SJC"].id
            hcm_id = catalog.locations_by_code["HCM"].id
            await cleanup_session.execute(
                delete(DailyPriceSeries).where(
                    DailyPriceSeries.commodity_id == sjc_id,
                    DailyPriceSeries.location_id == hcm_id,
                    DailyPriceSeries.price_date.in_([d_vcb_present, d_vcb_only_gap, today]),
                )
            )
            if backup_sjc:
                await cleanup_session.execute(pg_insert(DailyPriceSeries).values(backup_sjc))
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_news_sync_dedup_gemini_429_fallback_and_news_analytics_api() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    now_vn = vn_now()
    pub_recent = (now_vn - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S +0700")
    pub_gmt = (now_vn.astimezone(UTC) - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")

    rss_1 = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>M8TEST Vàng SJC bật tăng theo kỳ vọng Fed cắt giảm lãi suất</title>
    <description>Vàng thế giới vượt mốc khiến giá vàng SJC tăng mạnh.</description>
    <link>https://vnexpress.net/m8test-vang-sjc-1.html?utm_source=rss</link>
    <pubDate>{pub_recent}</pubDate>
  </item>
  <item>
    <title>M8TEST OPEC+ cắt giảm sản lượng giữa căng thẳng Trung Đông</title>
    <description>Giá dầu Brent leo thang mạnh, tác động tới giá xăng dầu.</description>
    <link>https://vnexpress.net/m8test-opec-xang-dau-2.html</link>
    <pubDate>{pub_recent}</pubDate>
  </item>
  <item>
    <title>M8TEST Saudi Aramco chốt giá CP giảm, giá gas đầu tháng hạ nhiệt</title>
    <description>Bình gas 12kg trong nước giảm theo giá CP thế giới.</description>
    <link>https://vnexpress.net/m8test-gas-aramco-3.html</link>
    <pubDate>{pub_recent}</pubDate>
  </item>
  <item>
    <title>M8TEST Dự án công viên cây xanh chuẩn bị khánh thành</title>
    <description>Tin đô thị không liên quan hàng hóa.</description>
    <link>https://vnexpress.net/m8test-cong-vien-4.html</link>
    <pubDate>{pub_recent}</pubDate>
  </item>
</channel></rss>""".encode()

    # Duplicates headline #1 via Google News RSS
    rss_gnews = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>M8TEST Vàng SJC bật tăng theo kỳ vọng Fed cắt giảm lãi suất - VnExpress</title>
    <description>Tin trùng tiêu đề từ Google News.</description>
    <link>https://news.google.com/rss/articles/m8test-dup-1?oc=5</link>
    <pubDate>{pub_gmt}</pubDate>
    <source url="https://vnexpress.net">VnExpress</source>
  </item>
</channel></rss>""".encode()

    feeds = (
        RssFeedSpec("TEST_VNE", "VnExpress", "https://vnexpress.test/rss.xml"),
        RssFeedSpec("GNEWS_GOLD", "Google News", "https://gnews.test/gold.xml"),
        RssFeedSpec("FAILING_FEED", "Broken", "https://broken.test/503.xml"),
    )

    test_urls = [
        "https://vnexpress.net/m8test-vang-sjc-1.html",
        "https://vnexpress.net/m8test-opec-xang-dau-2.html",
        "https://vnexpress.net/m8test-gas-aramco-3.html",
        "https://news.google.com/rss/articles/m8test-dup-1",
    ]

    try:
        def handler(request: httpx.Request) -> httpx.Response:
            u = str(request.url)
            if "vnexpress.test" in u:
                return httpx.Response(200, content=rss_1)
            if "gnews.test" in u:
                return httpx.Response(200, content=rss_gnews)
            if "generativelanguage.googleapis.com" in u:
                return httpx.Response(429, json={"error": {"code": 429}})
            return httpx.Response(503)

        settings = WorkerSettings.model_validate(
            {
                "DATABASE_URL": _HOST_TEST_DB_URL,
                "DATABASE_URL_SYNC": None,
                "GEMINI_API_KEY": "fake-key-for-429-fallback-test",
            }
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res1 = await run_news_sync(
                engine,
                session_factory,
                settings=settings,
                hours=72,
                feeds=feeds,
                enable_gemini=True,
                advisory_lock_key=908_712_912,
                http_client=client,
                delay_ms=0,
                reference_now_vn=now_vn,
            )
            res2 = await run_news_sync(
                engine,
                session_factory,
                settings=settings,
                hours=72,
                feeds=feeds,
                enable_gemini=True,
                advisory_lock_key=908_712_912,
                http_client=client,
                delay_ms=0,
                reference_now_vn=now_vn,
            )

        assert res1.feeds_ok == 2
        assert res1.feeds_failed == 1
        assert res1.inserted_count == 3  # 1 noise rejected + 1 Google News duplicate dropped
        assert res1.gemini_stats["fallback"] >= 1
        assert res2.inserted_count == 0
        assert res2.gemini_stats["attempted"] == 0

        app = create_app(_make_api_settings())
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as api_client,
        ):
            all_resp = await api_client.get("/api/v1/analytics/news?domain=ALL&hours=24&limit=100")
            assert all_resp.status_code == 200
            all_data = all_resp.json()
            matched_titles = [i["title"] for i in all_data["items"] if "M8TEST" in i["title"]]
            assert len(matched_titles) == 3

            gold_resp = await api_client.get("/api/v1/analytics/news?domain=GOLD&hours=24&limit=100")
            assert gold_resp.status_code == 200
            gold_items = [i for i in gold_resp.json()["items"] if "M8TEST" in i["title"]]
            assert len(gold_items) == 1
            assert "FED_RATE" in gold_items[0]["catalysts"]
            assert gold_items[0]["scoring_engine"] == "LEXICON_V1"

            bad_hours = await api_client.get("/api/v1/analytics/news?hours=48")
            assert bad_hours.status_code == 422
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(MarketNewsSignal).where(MarketNewsSignal.url.in_(test_urls))
            )
            await cleanup_session.commit()
        await engine.dispose()
