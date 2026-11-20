"""Offline unit tests for Milestone 7 historical backfill parsers and fidelity rules."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from vxg_worker.history.backfill_fuel import (
    extract_saigonpetro_fuel_notice_urls,
    parse_saigonpetro_fuel_notice,
)
from vxg_worker.history.backfill_gas import (
    extract_saigonpetro_gas_notice_urls,
    extract_vietnambiz_gas_article_candidates,
    parse_saigonpetro_gas_notice,
    parse_vietnambiz_gas_article,
)
from vxg_worker.history.backfill_gold import (
    CAFEF_GOLD_HISTORY_URL,
    WEBGIA_SJC_1Y_URL,
    parse_cafef_gold_history,
    parse_webgia_sjc_history,
)
from vxg_worker.history.rollup import compute_reference_price, subtract_months

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def test_subtract_months_and_compute_reference_price() -> None:
    assert subtract_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert subtract_months(date(2026, 11, 20), 24) == date(2024, 11, 20)
    assert compute_reference_price(133_700_000, 136_700_000, None) == 136_700_000
    assert compute_reference_price(None, None, 21_500) == 21_500


def test_cafef_gold_history_backfill_strict_hcm_only_and_dedup() -> None:
    raw = (FIXTURES_DIR / "gold/cafef_gold.json").read_bytes()
    points = parse_cafef_gold_history(
        raw,
        source_url=CAFEF_GOLD_HISTORY_URL,
        months=24,
        as_of_date=date(2026, 11, 20),
    )
    assert len(points) >= 150
    # Strict geographic fidelity: CafeF does not report HN/DN regional SJC branches separately
    assert {pt.location_code for pt in points} == {"HCM"}
    assert {pt.commodity_code for pt in points} == {"GOLD_SJC_BAR_SJC"}
    # Chronological uniqueness per day
    dates = [pt.price_date for pt in points]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))

    latest = points[-1]
    assert latest.price_date == date(2026, 11, 19)
    assert latest.buy_price_vnd == 133_700_000
    assert latest.sell_price_vnd == 136_700_000
    assert latest.retail_price_vnd is None
    assert latest.evidence_sha256 is not None and len(latest.evidence_sha256) == 64

    # Intraday duplicate: keep latest timestamp on same VN day
    intraday_payload = {
        "Data": {
            "goldPriceWorldHistories": [
                {"name": "SJC", "buyPrice": 133.0, "sellPrice": 136.0, "createdAt": "2026-11-19T02:00:00Z"},
                {"name": "SJC", "buyPrice": 134.2, "sellPrice": 137.2, "createdAt": "2026-11-19T09:00:00Z"},
                {"name": "SJC", "buyPrice": 999.0, "sellPrice": 999.0, "createdAt": "2026-11-19T10:00:00Z"},  # out of bounds
            ]
        }
    }
    deduped = parse_cafef_gold_history(
        json.dumps(intraday_payload).encode("utf-8"),
        as_of_date=date(2026, 11, 20),
    )
    assert len(deduped) == 1
    assert deduped[0].buy_price_vnd == 134_200_000
    assert deduped[0].sell_price_vnd == 137_200_000


def test_webgia_sjc_1y_backfill_strict_hcm_and_dates() -> None:
    raw = (FIXTURES_DIR / "history/webgia_sjc_1y_sample.html").read_bytes()
    points = parse_webgia_sjc_history(
        raw,
        source_url=WEBGIA_SJC_1Y_URL,
        months=12,
        as_of_date=date(2026, 11, 20),
    )
    assert len(points) == 5
    assert {pt.location_code for pt in points} == {"HCM"}
    assert points[0].price_date == date(2026, 11, 16)
    assert points[0].buy_price_vnd == 135_000_000
    assert points[0].sell_price_vnd == 138_000_000
    assert points[-1].price_date == date(2026, 11, 20)
    assert points[-1].buy_price_vnd == 131_200_000
    assert points[-1].sell_price_vnd == 134_200_000


def test_saigonpetro_fuel_archive_backfill_vung1_and_hyphen_date() -> None:
    listing_bytes = (FIXTURES_DIR / "fuel/saigonpetro_fuel_list.html").read_bytes()
    urls = extract_saigonpetro_fuel_notice_urls(listing_bytes)
    assert len(urls) >= 1
    assert all("gia-ban-le-mat-hang-xang-dau-" in u for u in urls)

    detail_bytes = (FIXTURES_DIR / "fuel/saigonpetro_fuel_detail.html").read_bytes()
    pts = parse_saigonpetro_fuel_notice(
        detail_bytes,
        detail_url="https://saigonpetro.com.vn/gia-ban-le-mat-hang-xang-dau-ngay-12-11-2026",
    )
    assert len(pts) >= 3
    assert {p.location_code for p in pts} == {"VUNG_1"}
    assert all(p.is_adjustment_day is True for p in pts)
    assert all(p.buy_price_vnd is None and p.sell_price_vnd is None and p.retail_price_vnd for p in pts)

    # Verify hyphenated legacy title date fallback (e.g., 09-10-2025)
    legacy_html = (
        b"<html><head><title>Gia ban le ngay 09-10-2025</title></head>"
        b"<body><h1>Gi\xc3\xa1 b\xc3\xa1n l\xe1\xba\xbb m\xe1\xba\xb7t h\xc3\xa0ng x\xc4\x83ng d\xe1\xba\xa7u ng\xc3\xa0y 09-10-2025</h1>"
        b"<div id='toc-content'><table>"
        b"<tr><th>Mat hang</th><th>Gia ban le (VND/lit)</th></tr>"
        b"<tr><td>X\xc4\x83ng RON 95-III</td><td>20.310</td></tr>"
        b"<tr><td>X\xc4\x83ng E5 RON 92-II</td><td>19.650</td></tr>"
        b"<tr><td>D\xe1\xba\xa7u DO 0,05S-II</td><td>18.420</td></tr>"
        b"</table></div></body></html>"
    )
    legacy_pts = parse_saigonpetro_fuel_notice(
        legacy_html,
        detail_url="https://saigonpetro.com.vn/gia-ban-le-mat-hang-xang-dau-ngay-09-10-2025",
        fallback_as_of=date(2026, 11, 20),
    )
    assert len(legacy_pts) == 3
    assert {p.price_date for p in legacy_pts} == {date(2025, 10, 9)}
    assert {p.location_code for p in legacy_pts} == {"VUNG_1"}


def test_saigonpetro_and_vietnambiz_gas_backfill_geographic_fidelity() -> None:
    sp_list = (FIXTURES_DIR / "gas/saigonpetro_gas_list.html").read_bytes()
    sp_urls = extract_saigonpetro_gas_notice_urls(sp_list)
    assert len(sp_urls) >= 1

    sp_detail = (FIXTURES_DIR / "gas/saigonpetro_gas_detail.html").read_bytes()
    sp_pts = parse_saigonpetro_gas_notice(
        sp_detail,
        detail_url="https://saigonpetro.com.vn/gia-ban-le-gas-tu-ngay-01112026",
    )
    assert len(sp_pts) == 2
    assert {p.location_code for p in sp_pts} == {"HCM"}
    assert {p.commodity_code for p in sp_pts} == {"GAS_SAIGONPETRO_12KG", "GAS_SAIGONPETRO_45KG"}
    assert all(p.is_adjustment_day is True for p in sp_pts)

    vb_list = (FIXTURES_DIR / "gas/vietnambiz_gas_list.html").read_bytes()
    vb_candidates = extract_vietnambiz_gas_article_candidates(vb_list)
    assert len(vb_candidates) >= 1

    # Unpadded VietNamBiz slug timestamps (e.g. Sept 16 -> 2026916..., July 31 -> 2026731..., Jan 15 -> 2026115...)
    synthetic_listing = (
        b"<html><body>"
        b"<h3><a href='/gia-gas-hom-nay-169-so-sanh-2026916101955275.htm'>Sept 16</a></h3>"
        b"<h3><a href='/gia-gas-hom-nay-317-cap-nhat-202673195525505.htm'>Jul 31</a></h3>"
        b"<h3><a href='/gia-gas-hom-nay-911-cap-nhat-20261199137877.htm'>Nov 9</a></h3>"
        b"<h3><a href='/gia-gas-hom-nay-151-cap-nhat-2026115101500000.htm'>Jan 15</a></h3>"
        b"<h3><a href='/gia-gas-hom-nay-211-cap-nhat-202611295633685.htm'>Nov 2</a></h3>"
        b"<h3><a href='/gia-gas-hom-nay-111-cap-nhat-202611109123456.htm'>Ambiguous 1/11 or 11/1</a></h3>"
        b"</body></html>"
    )
    hints = extract_vietnambiz_gas_article_candidates(synthetic_listing)
    assert [ym for _, ym in hints] == [
        (2026, 9),
        (2026, 7),
        (2026, 11),
        (2026, 1),
        (2026, 11),
        None,
    ]

    vb_detail = (FIXTURES_DIR / "gas/vietnambiz_gas_detail.html").read_bytes()
    vb_pts = parse_vietnambiz_gas_article(
        vb_detail,
        detail_url="https://vietnambiz.vn/gia-gas-hom-nay-1911-sample.htm",
    )
    assert len(vb_pts) >= 4
    assert all(p.location_code in {"HN", "DN", "HCM"} for p in vb_pts)

