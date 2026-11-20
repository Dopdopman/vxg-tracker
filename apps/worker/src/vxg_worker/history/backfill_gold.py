"""Historical Gold (SJC) backfill parsers & fetcher (CafeF 21m + Webgia 1y backup)."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import to_vn_tz, vn_today
from vxg_worker.history.rollup import (
    BackfillPricePoint,
    fetch_with_polite_retry,
    persist_backfill_points,
    subtract_months,
)
from vxg_worker.persistence import CatalogLookup

logger = logging.getLogger("vxg_worker.history.gold")

CAFEF_GOLD_HISTORY_URL = "https://cafef.vn/du-lieu/Ajax/ajaxgoldpricehistory.ashx?index=all"
CAFEF_GOLD_REFERER = "https://cafef.vn/du-lieu/gia-vang-hom-nay/trong-nuoc.chn"
WEBGIA_SJC_1Y_URL = "https://webgia.com/gia-vang/sjc/bieu-do-1-nam.html"

_GOLD_MIN_VND = 30_000_000
_GOLD_MAX_VND = 300_000_000
_GOLD_MAX_SPREAD_RATIO = 0.10

_WEBGIA_SERIES_RE = re.compile(r'\{name:"([^"]+)",data:(\[\[.*?\]\])')
_WEBGIA_PAIR_RE = re.compile(r"\[(\d+),([\d.]+)\]")


def _to_gold_vnd(raw_value: Any) -> int | None:
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if val < 1_000:
        return int(round(val * 1_000_000))
    if val < 1_000_000:
        return int(round(val * 1_000))
    return int(round(val))


def _is_valid_gold_pair(buy_vnd: int | None, sell_vnd: int | None) -> bool:
    if buy_vnd is None or sell_vnd is None:
        return False
    if not (_GOLD_MIN_VND <= buy_vnd <= sell_vnd <= _GOLD_MAX_VND):
        return False
    return ((sell_vnd - buy_vnd) / sell_vnd) <= _GOLD_MAX_SPREAD_RATIO


def parse_cafef_gold_history(
    raw_bytes: bytes,
    *,
    source_url: str = CAFEF_GOLD_HISTORY_URL,
    months: int = 24,
    as_of_date: date | None = None,
) -> list[BackfillPricePoint]:
    """Parse CafeF SJC gold history strictly for HCM (never cloned to HN/DN)."""
    payload: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    histories = payload.get("Data", {}).get("goldPriceWorldHistories")
    if not isinstance(histories, list) or not histories:
        raise ValueError("CafeF gold history JSON missing Data.goldPriceWorldHistories")

    ref_today = as_of_date or vn_today()
    cutoff_date = subtract_months(ref_today, months)
    sha256 = compute_content_sha256(raw_bytes)

    latest_by_day: dict[date, tuple[datetime, int, int, str]] = {}
    for item in histories:
        name = str(item.get("name") or "").strip().upper()
        if name != "SJC":
            continue
        buy_vnd = _to_gold_vnd(item.get("buyPrice"))
        sell_vnd = _to_gold_vnd(item.get("sellPrice"))
        if not _is_valid_gold_pair(buy_vnd, sell_vnd):
            continue
        assert buy_vnd is not None and sell_vnd is not None

        created_raw = str(item.get("createdAt") or "").strip()
        if not created_raw:
            continue
        try:
            parsed_utc = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            vn_dt = to_vn_tz(parsed_utc)
        except ValueError:
            continue

        p_date = vn_dt.date()
        if p_date < cutoff_date or p_date > ref_today:
            continue

        prev = latest_by_day.get(p_date)
        if prev is None or vn_dt > prev[0]:
            latest_by_day[p_date] = (vn_dt, buy_vnd, sell_vnd, created_raw)

    points: list[BackfillPricePoint] = []
    prev_sell: int | None = None
    for p_date in sorted(latest_by_day):
        vn_dt, buy_vnd, sell_vnd, created_raw = latest_by_day[p_date]
        is_adj = prev_sell is None or sell_vnd != prev_sell
        prev_sell = sell_vnd
        points.append(
            BackfillPricePoint(
                commodity_code="GOLD_SJC_BAR_SJC",
                location_code="HCM",
                price_date=p_date,
                buy_price_vnd=buy_vnd,
                sell_price_vnd=sell_vnd,
                retail_price_vnd=None,
                is_adjustment_day=is_adj,
                source_code="CAFEF_GOLD_BACKFILL",
                source_url=source_url,
                evidence_sha256=sha256,
                metadata_json={
                    "created_at": created_raw,
                    "published_at_vn": vn_dt.isoformat(),
                    "geographic_scope": "HCM_ONLY",
                },
            )
        )
    return points


def parse_webgia_sjc_history(
    raw_bytes: bytes,
    *,
    source_url: str = WEBGIA_SJC_1Y_URL,
    months: int = 12,
    as_of_date: date | None = None,
) -> list[BackfillPricePoint]:
    """Parse Webgia SJC 1-year Highcharts seriesOptions strictly for HCM backup gaps."""
    html_text = raw_bytes.decode("utf-8", errors="replace")
    series_matches = _WEBGIA_SERIES_RE.findall(html_text)
    if not series_matches:
        raise ValueError("Webgia SJC chart HTML missing seriesOptions data")

    ref_today = as_of_date or vn_today()
    cutoff_date = subtract_months(ref_today, months)
    sha256 = compute_content_sha256(raw_bytes)

    buy_by_date: dict[date, int] = {}
    sell_by_date: dict[date, int] = {}

    for series_name, data_blob in series_matches:
        lowered = series_name.lower()
        target_map: dict[date, int] | None = None
        if "mua" in lowered:
            target_map = buy_by_date
        elif "bán" in lowered or "ban" in lowered:
            target_map = sell_by_date
        if target_map is None:
            continue

        for ms_str, price_str in _WEBGIA_PAIR_RE.findall(data_blob):
            ms_val = int(ms_str)
            # Webgia chart encodes Vietnamese calendar date at 17:00 UTC on that exact day.
            p_date = datetime.fromtimestamp(ms_val / 1000.0, tz=UTC).date()
            if p_date < cutoff_date or p_date > ref_today:
                continue
            price_vnd = _to_gold_vnd(price_str)
            if price_vnd is not None:
                target_map[p_date] = price_vnd

    points: list[BackfillPricePoint] = []
    prev_sell: int | None = None
    for p_date in sorted(set(buy_by_date) & set(sell_by_date)):
        buy_vnd = buy_by_date[p_date]
        sell_vnd = sell_by_date[p_date]
        if not _is_valid_gold_pair(buy_vnd, sell_vnd):
            continue
        is_adj = prev_sell is None or sell_vnd != prev_sell
        prev_sell = sell_vnd
        points.append(
            BackfillPricePoint(
                commodity_code="GOLD_SJC_BAR_SJC",
                location_code="HCM",
                price_date=p_date,
                buy_price_vnd=buy_vnd,
                sell_price_vnd=sell_vnd,
                retail_price_vnd=None,
                is_adjustment_day=is_adj,
                source_code="WEBGIA_SJC_BACKFILL",
                source_url=source_url,
                evidence_sha256=sha256,
                metadata_json={"geographic_scope": "HCM_ONLY", "backup_series": True},
            )
        )
    return points


async def backfill_gold_history(
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    catalog: CatalogLookup,
    *,
    months: int = 24,
    include_webgia_backup: bool = True,
) -> dict[str, int]:
    """Fetch and persist real historical SJC gold prices (CafeF primary + Webgia backup)."""
    stats = {"cafef_parsed": 0, "cafef_upserted": 0, "webgia_parsed": 0, "webgia_upserted": 0}

    resp = await fetch_with_polite_retry(
        http_client,
        CAFEF_GOLD_HISTORY_URL,
        referer=CAFEF_GOLD_REFERER,
        accept_json=True,
    )
    resp.raise_for_status()
    cafef_points = parse_cafef_gold_history(
        resp.content,
        source_url=str(resp.url),
        months=months,
    )
    stats["cafef_parsed"] = len(cafef_points)
    stats["cafef_upserted"] = await persist_backfill_points(
        session,
        cafef_points,
        catalog,
        overwrite_existing_backfill=True,
    )

    if include_webgia_backup:
        try:
            wg_resp = await fetch_with_polite_retry(
                http_client,
                WEBGIA_SJC_1Y_URL,
                referer="https://webgia.com/gia-vang/sjc/",
            )
            if wg_resp.status_code == 200:
                wg_points = parse_webgia_sjc_history(
                    wg_resp.content,
                    source_url=str(wg_resp.url),
                    months=min(months, 12),
                )
                stats["webgia_parsed"] = len(wg_points)
                stats["webgia_upserted"] = await persist_backfill_points(
                    session,
                    wg_points,
                    catalog,
                    overwrite_existing_backfill=False,
                )
        except Exception as exc:
            logger.warning("Webgia SJC backup backfill skipped due to error: %s", exc)

    return stats

