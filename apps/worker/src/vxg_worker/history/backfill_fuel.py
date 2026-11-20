"""Historical Fuel (VUNG_1) backfill from Saigon Petro official paginated archive."""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.models import Source
from vxg_shared.enums import CommodityCategory, FetchStatus, FetchStrategy, FuelZoneCode, SourceTier
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import VN_TZ, vn_today
from vxg_worker.adapters.base import SchemaDriftError
from vxg_worker.adapters.common import normalize_space, parse_html, resolve_absolute_url
from vxg_worker.adapters.fuel.saigonpetro_fuel import SaigonPetroFuelAdapter
from vxg_worker.fetcher.types import FetcherTier, FetchResult
from vxg_worker.history.rollup import (
    BackfillPricePoint,
    extract_flexible_vn_date,
    fetch_with_polite_retry,
    persist_backfill_points,
    subtract_months,
)
from vxg_worker.persistence import CatalogLookup

logger = logging.getLogger("vxg_worker.history.fuel")

SAIGONPETRO_FUEL_ARCHIVE_URL = "https://saigonpetro.com.vn/gia-ban-le-mat-hang-xang-dau"

_FUEL_MIN_VND = 10_000
_FUEL_MAX_VND = 60_000


def _synthetic_saigonpetro_fuel_source() -> Source:
    return Source(
        id=0,
        code="SAIGONPETRO_FUEL",
        name="Saigon Petro — Backfill Lịch sử Xăng dầu",
        category=CommodityCategory.FUEL.value,
        tier=SourceTier.PRIMARY_OFFICIAL.value,
        base_url="https://saigonpetro.com.vn",
        endpoint_url=SAIGONPETRO_FUEL_ARCHIVE_URL,
        fetch_strategy=FetchStrategy.HTTP_HTML.value,
        parser_key="fuel.saigonpetro_fuel",
        schedule_cron="10 7 * * *",
        timeout_ms=20000,
        rate_limit_delay_ms=1000,
        is_active=True,
    )


def extract_saigonpetro_fuel_notice_urls(
    listing_bytes: bytes,
    *,
    base_url: str = SAIGONPETRO_FUEL_ARCHIVE_URL,
) -> list[str]:
    """Extract ordered unique fuel notice detail URLs from a Saigon Petro archive page."""
    tree = parse_html(listing_bytes)
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if "gia-ban-le-mat-hang-xang-dau-" not in href:
            continue
        abs_url = resolve_absolute_url(href, base_url)
        if abs_url not in seen:
            seen.add(abs_url)
            urls.append(abs_url)
    return urls


def parse_saigonpetro_fuel_notice(
    detail_bytes: bytes,
    *,
    detail_url: str,
    fallback_as_of: date | None = None,
) -> list[BackfillPricePoint]:
    """Parse one Saigon Petro fuel adjustment notice strictly as VUNG_1 points."""
    sha256 = compute_content_sha256(detail_bytes)
    fetch_res = FetchResult(
        request_url=detail_url,
        final_url=detail_url,
        http_status=200,
        content_type="text/html; charset=utf-8",
        raw_bytes=detail_bytes,
        content_sha256=sha256,
        byte_size=len(detail_bytes),
        response_time_ms=10,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.SUCCESS,
    )
    adapter = SaigonPetroFuelAdapter()
    parsed = adapter.parse(fetch_res, _synthetic_saigonpetro_fuel_source())

    # If the official notice uses hyphenated dates like '09-10-2025' not matched by '/' or '.',
    # extract the true notice date from heading/content/slug so we never misassign today's date.
    title_date = extract_flexible_vn_date(str(parsed.parser_metadata.get("document_title") or ""))
    slug_date = extract_flexible_vn_date(detail_url)
    tree = parse_html(detail_bytes)
    toc = tree.css_first("#toc-content")
    toc_date = extract_flexible_vn_date(normalize_space(toc.text(strip=True))) if toc else None
    resolved_date = toc_date or title_date or slug_date

    points: list[BackfillPricePoint] = []
    for cand in parsed.candidates:
        if cand.location_code != FuelZoneCode.VUNG_1.value:
            continue
        retail = cand.retail_price_vnd
        if not isinstance(retail, int) or not (_FUEL_MIN_VND <= retail <= _FUEL_MAX_VND):
            continue
        eff_date = cand.effective_date
        if (cand.source_published_at is None or eff_date == (fallback_as_of or vn_today())) and resolved_date is not None:
            eff_date = resolved_date
        pub_iso = (
            cand.source_published_at.isoformat()
            if isinstance(cand.source_published_at, datetime)
            else datetime(eff_date.year, eff_date.month, eff_date.day, 15, 0, tzinfo=VN_TZ).isoformat()
        )
        points.append(
            BackfillPricePoint(
                commodity_code=cand.commodity_code,
                location_code=FuelZoneCode.VUNG_1.value,
                price_date=eff_date,
                buy_price_vnd=None,
                sell_price_vnd=None,
                retail_price_vnd=retail,
                is_adjustment_day=True,
                source_code="SAIGONPETRO_FUEL_BACKFILL",
                source_url=detail_url,
                evidence_sha256=sha256,
                metadata_json={
                    "document_title": parsed.parser_metadata.get("document_title"),
                    "published_at_vn": pub_iso,
                    "geographic_scope": FuelZoneCode.VUNG_1.value,
                },
            )
        )
    return points


async def backfill_fuel_history(
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    catalog: CatalogLookup,
    *,
    months: int = 24,
    max_pages: int = 10,
    delay_ms: int = 350,
) -> dict[str, int]:
    """Fetch Saigon Petro historical adjustment notices across paginated pages into `daily_price_series`."""
    ref_today = vn_today()
    cutoff_date = subtract_months(ref_today, months)

    seen_urls: set[str] = set()
    all_points: list[BackfillPricePoint] = []
    stats = {"pages_scanned": 0, "notices_fetched": 0, "points_parsed": 0, "upserted": 0}

    for page in range(1, max_pages + 1):
        list_url = (
            SAIGONPETRO_FUEL_ARCHIVE_URL
            if page == 1
            else f"{SAIGONPETRO_FUEL_ARCHIVE_URL}?page={page}"
        )
        list_resp = await fetch_with_polite_retry(
            http_client,
            list_url,
            referer=SAIGONPETRO_FUEL_ARCHIVE_URL,
            delay_ms=delay_ms,
        )
        if list_resp.status_code != 200:
            break

        urls = extract_saigonpetro_fuel_notice_urls(list_resp.content, base_url=str(list_resp.url))
        new_urls = [u for u in urls if u not in seen_urls]
        if not new_urls:
            break

        stats["pages_scanned"] += 1
        any_in_window = False
        for detail_url in new_urls:
            seen_urls.add(detail_url)
            try:
                detail_resp = await fetch_with_polite_retry(
                    http_client,
                    detail_url,
                    referer=list_url,
                    delay_ms=delay_ms,
                )
                if detail_resp.status_code != 200:
                    continue
                stats["notices_fetched"] += 1
                pts = parse_saigonpetro_fuel_notice(
                    detail_resp.content,
                    detail_url=str(detail_resp.url),
                    fallback_as_of=ref_today,
                )
                in_window = [p for p in pts if cutoff_date <= p.price_date <= ref_today]
                if in_window:
                    any_in_window = True
                    all_points.extend(in_window)
            except (SchemaDriftError, Exception) as exc:
                logger.warning("Skipping Saigon Petro fuel notice %s: %s", detail_url, exc)

        if not any_in_window and page > 1:
            break

    stats["points_parsed"] = len(all_points)
    stats["upserted"] = await persist_backfill_points(
        session,
        all_points,
        catalog,
        overwrite_existing_backfill=True,
    )
    return stats
