"""Historical Gas backfill from Saigon Petro official archive (HCM) & VietNamBiz archive."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.models import Source
from vxg_shared.enums import CommodityCategory, FetchStatus, FetchStrategy, SourceTier
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import VN_TZ, vn_today
from vxg_worker.adapters.base import SchemaDriftError
from vxg_worker.adapters.common import normalize_space, parse_html, resolve_absolute_url
from vxg_worker.adapters.gas.saigonpetro_gas import SaigonPetroGasAdapter
from vxg_worker.adapters.gas.vietnambiz_gas import VietnamBizGasCrossCheckAdapter
from vxg_worker.fetcher.types import FetcherTier, FetchResult
from vxg_worker.history.rollup import (
    BackfillPricePoint,
    extract_flexible_vn_date,
    fetch_with_polite_retry,
    persist_backfill_points,
    subtract_months,
)
from vxg_worker.persistence import CatalogLookup

logger = logging.getLogger("vxg_worker.history.gas")

SAIGONPETRO_GAS_ARCHIVE_URL = "https://saigonpetro.com.vn/gia-ban-le"
VIETNAMBIZ_GAS_ARCHIVE_URL = "https://vietnambiz.vn/gia-gas.html"

_GAS_12KG_BOUNDS = (200_000, 1_500_000)
_GAS_IND_BOUNDS = (800_000, 6_000_000)
_VNBIZ_TRAILING_ID_RE = re.compile(r"-(20\d{2})(\d+)\.htm$")
_VNBIZ_DM_SLUG_RE = re.compile(r"gia-gas-hom-nay-(\d{2,4})-")


def _extract_vnbiz_ym_hint(url: str) -> tuple[int, int] | None:
    """Safely extract a validated (year, month) hint from a VietNamBiz article URL."""
    id_match = _VNBIZ_TRAILING_ID_RE.search(url)
    if not id_match:
        return None
    year = int(id_match.group(1))
    tail = id_match.group(2)

    dm_match = _VNBIZ_DM_SLUG_RE.search(url)
    dm_token = dm_match.group(1) if dm_match else None

    candidates: list[tuple[int, int]] = []
    for month_len in (2, 1):
        if len(tail) <= month_len:
            continue
        month = int(tail[:month_len])
        if not (1 <= month <= 12):
            continue
        rest = tail[month_len:]
        for day_len in (2, 1):
            if len(rest) < day_len:
                continue
            day = int(rest[:day_len])
            try:
                date(year, month, day)
            except ValueError:
                continue
            if dm_token is None or dm_token in {
                f"{day}{month}",
                f"{day:02d}{month:02d}",
                f"{day:02d}{month}",
                f"{day}{month:02d}",
            }:
                candidates.append((year, month))

    distinct_ym = set(candidates)
    if len(distinct_ym) == 1:
        return distinct_ym.pop()
    return None


def _is_valid_gas_price(commodity_code: str, price_vnd: int | None) -> bool:
    if not isinstance(price_vnd, int):
        return False
    low, high = _GAS_12KG_BOUNDS if "12KG" in commodity_code else _GAS_IND_BOUNDS
    return low <= price_vnd <= high


def _synthetic_source(code: str, base_url: str, endpoint_url: str, parser_key: str, tier: str) -> Source:
    return Source(
        id=0,
        code=code,
        name=code,
        category=CommodityCategory.GAS.value,
        tier=tier,
        base_url=base_url,
        endpoint_url=endpoint_url,
        fetch_strategy=FetchStrategy.HTTP_HTML.value,
        parser_key=parser_key,
        schedule_cron="30 7 * * *",
        timeout_ms=20000,
        rate_limit_delay_ms=1000,
        is_active=True,
    )


def extract_saigonpetro_gas_notice_urls(
    listing_bytes: bytes,
    *,
    base_url: str = SAIGONPETRO_GAS_ARCHIVE_URL,
) -> list[str]:
    """Extract distinct Saigon Petro official Gas notice links."""
    tree = parse_html(listing_bytes)
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if "gia-ban-le-gas-tu-ngay-" not in href:
            continue
        abs_url = resolve_absolute_url(href, base_url)
        if abs_url not in seen:
            seen.add(abs_url)
            urls.append(abs_url)
    return urls


def parse_saigonpetro_gas_notice(
    detail_bytes: bytes,
    *,
    detail_url: str,
    fallback_as_of: date | None = None,
) -> list[BackfillPricePoint]:
    """Parse one Saigon Petro Gas official notice strictly for HCM."""
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
    adapter = SaigonPetroGasAdapter()
    src = _synthetic_source(
        "SAIGONPETRO_GAS",
        "https://saigonpetro.com.vn",
        SAIGONPETRO_GAS_ARCHIVE_URL,
        "gas.saigonpetro_gas",
        SourceTier.PRIMARY_OFFICIAL.value,
    )
    parsed = adapter.parse(fetch_res, src)

    title_date = extract_flexible_vn_date(str(parsed.parser_metadata.get("document_title") or ""))
    slug_date = extract_flexible_vn_date(detail_url)
    tree = parse_html(detail_bytes)
    toc = tree.css_first("#toc-content")
    toc_date = extract_flexible_vn_date(normalize_space(toc.text(strip=True))) if toc else None
    resolved_date = title_date or toc_date or slug_date

    points: list[BackfillPricePoint] = []
    for cand in parsed.candidates:
        if cand.location_code != "HCM" or not _is_valid_gas_price(cand.commodity_code, cand.retail_price_vnd):
            continue
        assert isinstance(cand.retail_price_vnd, int)
        eff_date = cand.effective_date
        if (cand.source_published_at is None or eff_date == (fallback_as_of or vn_today())) and resolved_date is not None:
            eff_date = resolved_date
        pub_iso = (
            cand.source_published_at.isoformat()
            if isinstance(cand.source_published_at, datetime)
            else datetime(eff_date.year, eff_date.month, eff_date.day, 7, 30, tzinfo=VN_TZ).isoformat()
        )
        points.append(
            BackfillPricePoint(
                commodity_code=cand.commodity_code,
                location_code="HCM",
                price_date=eff_date,
                buy_price_vnd=None,
                sell_price_vnd=None,
                retail_price_vnd=cand.retail_price_vnd,
                is_adjustment_day=True,
                source_code="SAIGONPETRO_GAS_BACKFILL",
                source_url=detail_url,
                evidence_sha256=sha256,
                metadata_json={
                    "document_title": parsed.parser_metadata.get("document_title"),
                    "published_at_vn": pub_iso,
                    "geographic_scope": "HCM_ONLY",
                },
            )
        )
    return points


def extract_vietnambiz_gas_article_candidates(
    listing_bytes: bytes,
    *,
    base_url: str = VIETNAMBIZ_GAS_ARCHIVE_URL,
) -> list[tuple[str, tuple[int, int] | None]]:
    """Extract ordered VietNamBiz gas roundup article URLs and optional (year, month) hints."""
    tree = parse_html(listing_bytes)
    results: list[tuple[str, tuple[int, int] | None]] = []
    seen: set[str] = set()

    for anchor in tree.css("h2 a, h3 a, .title a"):
        href = (anchor.attributes.get("href") or "").strip()
        if "gia-gas-hom-nay" not in href and "gia-gas-thang" not in href:
            continue
        if not href.endswith(".htm"):
            continue
        abs_url = resolve_absolute_url(href, base_url)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        ym_hint = _extract_vnbiz_ym_hint(abs_url)
        results.append((abs_url, ym_hint))

    return results


def parse_vietnambiz_gas_article(
    detail_bytes: bytes,
    *,
    detail_url: str,
) -> list[BackfillPricePoint]:
    """Parse one VietNamBiz gas comparison article keeping only explicit city prices."""
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
    adapter = VietnamBizGasCrossCheckAdapter()
    src = _synthetic_source(
        "VIETNAMBIZ_GAS_CROSSCHECK",
        "https://vietnambiz.vn",
        VIETNAMBIZ_GAS_ARCHIVE_URL,
        "gas.vietnambiz_gas",
        SourceTier.SECONDARY_CROSSCHECK.value,
    )
    parsed = adapter.parse(fetch_res, src)
    points: list[BackfillPricePoint] = []
    for cand in parsed.candidates:
        if not _is_valid_gas_price(cand.commodity_code, cand.retail_price_vnd):
            continue
        assert isinstance(cand.retail_price_vnd, int)
        points.append(
            BackfillPricePoint(
                commodity_code=cand.commodity_code,
                location_code=cand.location_code,
                price_date=cand.effective_date,
                buy_price_vnd=None,
                sell_price_vnd=None,
                retail_price_vnd=cand.retail_price_vnd,
                is_adjustment_day=(cand.effective_date.day == 1),
                source_code="VIETNAMBIZ_GAS_BACKFILL",
                source_url=detail_url,
                evidence_sha256=sha256,
                metadata_json={"document_title": parsed.parser_metadata.get("document_title")},
            )
        )
    return points


async def backfill_gas_history(
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    catalog: CatalogLookup,
    *,
    months: int = 24,
    max_sp_pages: int = 4,
    max_vb_pages: int = 12,
    max_vb_articles_per_month: int = 3,
    delay_ms: int = 350,
) -> dict[str, int]:
    """Backfill Gas from Saigon Petro official archive (HCM) and VietNamBiz regional archive."""
    ref_today = vn_today()
    cutoff_date = subtract_months(ref_today, months)

    stats = {
        "sp_notices": 0,
        "sp_upserted": 0,
        "vb_articles": 0,
        "vb_upserted": 0,
    }

    # 1. Official Saigon Petro notices (HCM only, takes priority over press roundups)
    seen_sp_urls: set[str] = set()
    sp_points: list[BackfillPricePoint] = []
    for page in range(1, max_sp_pages + 1):
        list_url = (
            SAIGONPETRO_GAS_ARCHIVE_URL
            if page == 1
            else f"{SAIGONPETRO_GAS_ARCHIVE_URL}?page={page}"
        )
        list_resp = await fetch_with_polite_retry(
            http_client,
            list_url,
            referer=SAIGONPETRO_GAS_ARCHIVE_URL,
            delay_ms=delay_ms,
        )
        if list_resp.status_code != 200:
            break
        urls = extract_saigonpetro_gas_notice_urls(list_resp.content, base_url=str(list_resp.url))
        new_urls = [u for u in urls if u not in seen_sp_urls]
        if not new_urls:
            break

        for detail_url in new_urls:
            seen_sp_urls.add(detail_url)
            try:
                d_resp = await fetch_with_polite_retry(
                    http_client,
                    detail_url,
                    referer=list_url,
                    delay_ms=delay_ms,
                )
                if d_resp.status_code != 200:
                    continue
                stats["sp_notices"] += 1
                pts = parse_saigonpetro_gas_notice(
                    d_resp.content,
                    detail_url=str(d_resp.url),
                    fallback_as_of=ref_today,
                )
                sp_points.extend(p for p in pts if cutoff_date <= p.price_date <= ref_today)
            except (SchemaDriftError, Exception) as exc:
                logger.warning("Skipping Saigon Petro gas notice %s: %s", detail_url, exc)

    stats["sp_upserted"] = await persist_backfill_points(
        session,
        sp_points,
        catalog,
        overwrite_existing_backfill=True,
    )

    # 2. VietNamBiz regional archive (fills Petrolimex HN/DN/HCM & earlier months politely)
    seen_vb_urls: set[str] = set()
    parsed_per_month: dict[tuple[int, int], int] = {}
    vb_raw_points: list[BackfillPricePoint] = []

    for page in range(1, max_vb_pages + 1):
        list_url = (
            VIETNAMBIZ_GAS_ARCHIVE_URL
            if page == 1
            else f"https://vietnambiz.vn/gia-gas/trang-{page}.html"
        )
        list_resp = await fetch_with_polite_retry(
            http_client,
            list_url,
            referer=VIETNAMBIZ_GAS_ARCHIVE_URL,
            delay_ms=delay_ms,
        )
        if list_resp.status_code != 200:
            break

        candidates = extract_vietnambiz_gas_article_candidates(
            list_resp.content,
            base_url=str(list_resp.url),
        )
        new_candidates = [(u, ym) for u, ym in candidates if u not in seen_vb_urls]
        if not new_candidates:
            continue

        for art_url, ym_hint in new_candidates:
            seen_vb_urls.add(art_url)
            if ym_hint is not None:
                try:
                    ym_date = date(ym_hint[0], ym_hint[1], 1)
                except ValueError:
                    ym_date = None
                if ym_date is not None:
                    if ym_date < date(cutoff_date.year, cutoff_date.month, 1):
                        continue
                    if parsed_per_month.get(ym_hint, 0) >= max_vb_articles_per_month:
                        continue

            try:
                art_resp = await fetch_with_polite_retry(
                    http_client,
                    art_url,
                    referer=list_url,
                    delay_ms=delay_ms,
                )
                if art_resp.status_code != 200:
                    continue
                pts = parse_vietnambiz_gas_article(art_resp.content, detail_url=str(art_resp.url))
                in_window = [p for p in pts if cutoff_date <= p.price_date <= ref_today]
                if in_window:
                    stats["vb_articles"] += 1
                    actual_ym = (in_window[0].price_date.year, in_window[0].price_date.month)
                    parsed_per_month[actual_ym] = parsed_per_month.get(actual_ym, 0) + 1
                    vb_raw_points.extend(in_window)
            except SchemaDriftError:
                continue
            except Exception as exc:
                logger.warning("Skipping VietNamBiz gas article %s: %s", art_url, exc)

    # Mark monthly price-change transitions chronologically per (commodity_code, location_code).
    by_series: dict[tuple[str, str], list[BackfillPricePoint]] = {}
    for pt in vb_raw_points:
        by_series.setdefault((pt.commodity_code, pt.location_code), []).append(pt)

    final_vb_points: list[BackfillPricePoint] = []
    for series_pts in by_series.values():
        series_pts.sort(key=lambda p: p.price_date)
        prev_retail: int | None = None
        for pt in series_pts:
            assert pt.retail_price_vnd is not None
            is_adj = pt.is_adjustment_day or prev_retail is None or pt.retail_price_vnd != prev_retail
            prev_retail = pt.retail_price_vnd
            final_vb_points.append(
                BackfillPricePoint(
                    commodity_code=pt.commodity_code,
                    location_code=pt.location_code,
                    price_date=pt.price_date,
                    buy_price_vnd=None,
                    sell_price_vnd=None,
                    retail_price_vnd=pt.retail_price_vnd,
                    is_adjustment_day=is_adj,
                    source_code=pt.source_code,
                    source_url=pt.source_url,
                    evidence_sha256=pt.evidence_sha256,
                    metadata_json=pt.metadata_json,
                )
            )

    stats["vb_upserted"] = await persist_backfill_points(
        session,
        final_vb_points,
        catalog,
        overwrite_existing_backfill=False,
    )
    return stats
