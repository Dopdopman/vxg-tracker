"""Daily normalized price rollup (Phase 1 -> Phase 2) and shared backfill upsert utilities."""

from __future__ import annotations

import asyncio
import calendar
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.models import DailyPriceSeries, PriceRecord, Source
from vxg_shared.enums import SeriesOrigin, SourceTier, VerificationStatus
from vxg_shared.timezone import vn_now
from vxg_worker.fetcher.headers import build_browser_headers
from vxg_worker.persistence import CatalogLookup

logger = logging.getLogger("vxg_worker.history")

_HYPHEN_DMY_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})")
_COMPACT_SLUG_DATE_RE = re.compile(r"(\d{2})(\d{2})(20\d{2})")

_PUBLIC_VERIFIED_STATUSES: tuple[str, ...] = (
    VerificationStatus.VERIFIED.value,
    VerificationStatus.CROSS_CHECKED.value,
    VerificationStatus.OVERRIDDEN_MANUAL.value,
)

# When multiple official Phase 1 sources publish on the same day for the same (commodity, zone),
# prefer the primary national benchmark retailer deterministically.
_SOURCE_ROLLUP_PRIORITY: dict[str, int] = {
    "SJC_OFFICIAL_XML": 0,
    "DOJI_BOARD": 1,
    "PNJ_HTML": 2,
    "PETROLIMEX_FUEL": 0,
    "SAIGONPETRO_FUEL": 1,
    "PVOIL_FUEL": 2,
    "PETROLIMEX_GAS": 0,
    "SAIGONPETRO_GAS": 1,
    "PVGAS_LPG": 2,
}

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class BackfillPricePoint:
    """Normalized historical price point produced by a backfill parser prior to DB upsert."""

    commodity_code: str
    location_code: str
    price_date: date
    buy_price_vnd: int | None
    sell_price_vnd: int | None
    retail_price_vnd: int | None
    is_adjustment_day: bool
    source_code: str
    source_url: str
    evidence_sha256: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


def subtract_months(ref_date: date, months: int) -> date:
    """Return calendar date exactly `months` prior to `ref_date` clamped to month length."""
    if months <= 0:
        return ref_date
    total_months = (ref_date.year * 12 + (ref_date.month - 1)) - months
    year, month_zero = divmod(total_months, 12)
    month = month_zero + 1
    day = min(ref_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def extract_flexible_vn_date(text: str) -> date | None:
    """Extract DD/MM/YYYY, DD.MM.YYYY, DD-MM-YYYY, or DDMMYYYY dates from VN notices/slugs."""
    if m := _HYPHEN_DMY_RE.search(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    if m := _COMPACT_SLUG_DATE_RE.search(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def compute_reference_price(
    buy_price_vnd: int | None,
    sell_price_vnd: int | None,
    retail_price_vnd: int | None,
) -> int:
    """Return canonical scalar VND price matching the BUY_SELL / SINGLE_RETAIL shape contract."""
    if retail_price_vnd is not None and buy_price_vnd is None and sell_price_vnd is None:
        if retail_price_vnd <= 0:
            raise ValueError(f"retail_price_vnd must be > 0, got {retail_price_vnd}")
        return retail_price_vnd
    if buy_price_vnd is not None and sell_price_vnd is not None and retail_price_vnd is None:
        if buy_price_vnd <= 0 or sell_price_vnd <= 0 or sell_price_vnd < buy_price_vnd:
            raise ValueError(
                f"Invalid BUY_SELL pair: buy={buy_price_vnd}, sell={sell_price_vnd}"
            )
        return sell_price_vnd
    raise ValueError(
        f"Invalid price shape (buy={buy_price_vnd}, sell={sell_price_vnd}, retail={retail_price_vnd})"
    )


async def fetch_with_polite_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    referer: str | None = None,
    accept_json: bool = False,
    max_attempts: int = 3,
    delay_ms: int = 350,
) -> httpx.Response:
    """Perform a polite rate-limited HTTP GET with browser headers and exponential backoff."""
    headers = build_browser_headers(url, referer=referer)
    if accept_json:
        headers["Accept"] = "application/json, text/plain, */*"

    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    last_exc: Exception | None = None
    last_response: httpx.Response | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(url, headers=headers)
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                return response
            last_response = response
            if attempt < max_attempts:
                retry_after_raw = response.headers.get("Retry-After", "").strip()
                backoff = float(retry_after_raw) if retry_after_raw.isdigit() else (1.5 ** attempt)
                await asyncio.sleep(min(backoff, 10.0) + random.uniform(0.1, 0.45))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                await asyncio.sleep((1.5 ** attempt) + random.uniform(0.1, 0.45))

    if last_response is not None:
        return last_response
    assert last_exc is not None
    raise last_exc


async def upsert_daily_price_series(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    *,
    allow_override_live: bool = False,
    overwrite_existing_backfill: bool = True,
) -> int:
    """Idempotently upsert `daily_price_series` batches honoring LIVE_ROLLUP precedence."""
    if not rows:
        return 0

    deduped: dict[tuple[int, int, date], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["commodity_id"]), int(row["location_id"]), row["price_date"])
        deduped[key] = row

    unique_rows = list(deduped.values())
    now_vn = vn_now()
    for row in unique_rows:
        row.setdefault("updated_at", now_vn)

    affected = 0
    chunk_size = 400
    for idx in range(0, len(unique_rows), chunk_size):
        chunk = unique_rows[idx : idx + chunk_size]
        stmt = pg_insert(DailyPriceSeries).values(chunk)
        update_cols = {
            "buy_price_vnd": stmt.excluded.buy_price_vnd,
            "sell_price_vnd": stmt.excluded.sell_price_vnd,
            "retail_price_vnd": stmt.excluded.retail_price_vnd,
            "reference_price_vnd": stmt.excluded.reference_price_vnd,
            "series_origin": stmt.excluded.series_origin,
            "is_adjustment_day": stmt.excluded.is_adjustment_day,
            "source_record_id": stmt.excluded.source_record_id,
            "source_code": stmt.excluded.source_code,
            "source_url": stmt.excluded.source_url,
            "evidence_sha256": stmt.excluded.evidence_sha256,
            "metadata_json": stmt.excluded.metadata_json,
            "updated_at": stmt.excluded.updated_at,
        }
        if allow_override_live:
            stmt = stmt.on_conflict_do_update(
                index_elements=["commodity_id", "location_id", "price_date"],
                set_=update_cols,
            )
        elif overwrite_existing_backfill:
            stmt = stmt.on_conflict_do_update(
                index_elements=["commodity_id", "location_id", "price_date"],
                set_=update_cols,
                where=(DailyPriceSeries.series_origin == SeriesOrigin.BACKFILL.value),
            )
        else:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["commodity_id", "location_id", "price_date"]
            )
        res = await session.execute(stmt)
        row_cnt = getattr(res, "rowcount", None)
        if isinstance(row_cnt, int) and row_cnt > 0:
            affected += row_cnt

    return affected


async def persist_backfill_points(
    session: AsyncSession,
    points: list[BackfillPricePoint],
    catalog: CatalogLookup,
    *,
    overwrite_existing_backfill: bool = True,
) -> int:
    """Resolve catalog codes and persist normalized BackfillPricePoints safely."""
    rows: list[dict[str, Any]] = []
    for pt in points:
        commodity = catalog.commodities_by_code.get(pt.commodity_code)
        location = catalog.locations_by_code.get(pt.location_code)
        if commodity is None or location is None:
            logger.debug(
                "Skipping unmapped backfill point commodity=%s location=%s",
                pt.commodity_code,
                pt.location_code,
            )
            continue
        ref_price = compute_reference_price(
            pt.buy_price_vnd,
            pt.sell_price_vnd,
            pt.retail_price_vnd,
        )
        rows.append(
            {
                "commodity_id": commodity.id,
                "location_id": location.id,
                "price_date": pt.price_date,
                "buy_price_vnd": pt.buy_price_vnd,
                "sell_price_vnd": pt.sell_price_vnd,
                "retail_price_vnd": pt.retail_price_vnd,
                "reference_price_vnd": ref_price,
                "series_origin": SeriesOrigin.BACKFILL.value,
                "is_adjustment_day": pt.is_adjustment_day,
                "source_record_id": None,
                "source_code": pt.source_code,
                "source_url": pt.source_url,
                "evidence_sha256": pt.evidence_sha256,
                "metadata_json": dict(pt.metadata_json),
            }
        )

    return await upsert_daily_price_series(
        session,
        rows,
        allow_override_live=False,
        overwrite_existing_backfill=overwrite_existing_backfill,
    )


def _record_rank(record: PriceRecord, source: Source) -> tuple[int, int, float, float]:
    tier_rank = 0 if source.tier == SourceTier.PRIMARY_OFFICIAL.value else 1
    source_rank = _SOURCE_ROLLUP_PRIORITY.get(source.code, 10 + source.id)
    pub_ts = (record.source_published_at or record.crawled_at).timestamp()
    crawl_ts = record.crawled_at.timestamp()
    return (tier_rank, source_rank, -pub_ts, -crawl_ts)


async def rollup_daily_from_price_records(
    session: AsyncSession,
    *,
    crawl_run_id: Any | None = None,
    since_date: date | None = None,
) -> int:
    """Promote clean VERIFIED/CROSS_CHECKED Phase 1 records into `daily_price_series`."""
    base_stmt = (
        select(PriceRecord, Source)
        .join(Source, PriceRecord.source_id == Source.id)
        .where(PriceRecord.verification_status.in_(_PUBLIC_VERIFIED_STATUSES))
    )

    if crawl_run_id is not None:
        touched_rows = (
            await session.execute(
                select(
                    PriceRecord.commodity_id,
                    PriceRecord.location_id,
                    PriceRecord.effective_date,
                )
                .where(
                    PriceRecord.crawl_run_id == crawl_run_id,
                    PriceRecord.verification_status.in_(_PUBLIC_VERIFIED_STATUSES),
                )
                .distinct()
            )
        ).all()
        if not touched_rows:
            return 0
        commodity_ids = {int(r[0]) for r in touched_rows}
        location_ids = {int(r[1]) for r in touched_rows}
        eff_dates = {r[2] for r in touched_rows}
        touched_keys = {(int(r[0]), int(r[1]), r[2]) for r in touched_rows}
        stmt = base_stmt.where(
            PriceRecord.commodity_id.in_(commodity_ids),
            PriceRecord.location_id.in_(location_ids),
            PriceRecord.effective_date.in_(eff_dates),
        )
    else:
        touched_keys = None
        stmt = base_stmt
        if since_date is not None:
            stmt = stmt.where(PriceRecord.effective_date >= since_date)

    best_by_key: dict[tuple[int, int, date], tuple[PriceRecord, Source]] = {}
    for record, source in (await session.execute(stmt)).all():
        key = (record.commodity_id, record.location_id, record.effective_date)
        if touched_keys is not None and key not in touched_keys:
            continue
        existing = best_by_key.get(key)
        if existing is None or _record_rank(record, source) < _record_rank(existing[0], existing[1]):
            best_by_key[key] = (record, source)

    if not best_by_key:
        return 0

    by_pair: dict[tuple[int, int], list[tuple[date, PriceRecord, Source]]] = {}
    for (cid, lid, eff_date), (record, source) in best_by_key.items():
        by_pair.setdefault((cid, lid), []).append((eff_date, record, source))

    rollup_rows: list[dict[str, Any]] = []
    now_vn = vn_now()

    for (cid, lid), entries in by_pair.items():
        entries.sort(key=lambda item: item[0])
        earliest_date = entries[0][0]
        prev_ref = (
            await session.execute(
                select(DailyPriceSeries.reference_price_vnd)
                .where(
                    DailyPriceSeries.commodity_id == cid,
                    DailyPriceSeries.location_id == lid,
                    DailyPriceSeries.price_date < earliest_date,
                )
                .order_by(DailyPriceSeries.price_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        last_ref: int | None = int(prev_ref) if prev_ref is not None else None
        for eff_date, record, source in entries:
            ref_price = compute_reference_price(
                record.buy_price_vnd,
                record.sell_price_vnd,
                record.retail_price_vnd,
            )
            is_adj = last_ref is None or ref_price != last_ref
            last_ref = ref_price
            rollup_rows.append(
                {
                    "commodity_id": cid,
                    "location_id": lid,
                    "price_date": eff_date,
                    "buy_price_vnd": record.buy_price_vnd,
                    "sell_price_vnd": record.sell_price_vnd,
                    "retail_price_vnd": record.retail_price_vnd,
                    "reference_price_vnd": ref_price,
                    "series_origin": SeriesOrigin.LIVE_ROLLUP.value,
                    "is_adjustment_day": is_adj,
                    "source_record_id": record.id,
                    "source_code": source.code,
                    "source_url": record.source_url,
                    "evidence_sha256": record.evidence_sha256,
                    "metadata_json": {
                        "crawl_run_id": str(record.crawl_run_id),
                        "source_tier": source.tier,
                        "verification_status": record.verification_status,
                        "published_at_vn": (
                            record.source_published_at.isoformat()
                            if isinstance(record.source_published_at, datetime)
                            else None
                        ),
                    },
                    "updated_at": now_vn,
                }
            )

    return await upsert_daily_price_series(
        session,
        rollup_rows,
        allow_override_live=True,
    )
