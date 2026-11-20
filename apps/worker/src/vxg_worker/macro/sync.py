"""Macro indicator sync orchestrator and idempotent PostgreSQL persistence."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vxg_shared.db.models import MacroIndicator
from vxg_shared.enums import MacroIndicatorCode
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.locks import PostgresAdvisoryLock
from vxg_worker.macro.types import MacroPoint, attach_change_pct_1d
from vxg_worker.macro.vietcombank_client import MAX_VCB_LOOKBACK_DAYS, fetch_vcb_rates_window
from vxg_worker.macro.yahoo_client import YAHOO_TICKER_MAP, fetch_yahoo_indicator

logger = logging.getLogger("vxg_worker.macro.sync")

DEFAULT_MACRO_LOCK_KEY = 908_712_004


@dataclass(slots=True)
class MacroSyncSummary:
    """Execution telemetry returned by sync-macro / backfill-macro."""

    mode: str
    duration_ms: int
    upserted_by_indicator: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


async def upsert_macro_indicators(session: AsyncSession, points: list[MacroPoint]) -> int:
    """Idempotently upsert macro observations by (indicator_code, indicator_date)."""
    if not points:
        return 0

    deduped: dict[tuple[str, date], MacroPoint] = {}
    for pt in points:
        deduped[(pt.indicator_code.value, pt.indicator_date)] = pt

    now_vn = vn_now()
    rows = [
        {
            "indicator_code": pt.indicator_code.value,
            "indicator_date": pt.indicator_date,
            "value": pt.value,
            "open_value": pt.open_value,
            "high_value": pt.high_value,
            "low_value": pt.low_value,
            "change_pct_1d": pt.change_pct_1d,
            "unit": pt.unit,
            "source_code": pt.source_code,
            "source_url": pt.source_url,
            "content_sha256": pt.content_sha256,
            "metadata_json": pt.metadata_json,
            "fetched_at": now_vn,
        }
        for pt in deduped.values()
    ]

    stmt = pg_insert(MacroIndicator).values(rows)
    excluded = stmt.excluded
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["indicator_code", "indicator_date"],
        set_={
            "value": excluded.value,
            "open_value": excluded.open_value,
            "high_value": excluded.high_value,
            "low_value": excluded.low_value,
            "change_pct_1d": excluded.change_pct_1d,
            "unit": excluded.unit,
            "source_code": excluded.source_code,
            "source_url": excluded.source_url,
            "content_sha256": excluded.content_sha256,
            "metadata_json": excluded.metadata_json,
            "fetched_at": excluded.fetched_at,
        },
    )
    await session.execute(upsert_stmt)
    return len(rows)


def _yahoo_range_for_run(mode: Literal["sync", "backfill"], months: int, sync_days: int) -> str:
    if mode == "backfill":
        if months <= 12:
            return "1y"
        if months <= 24:
            return "2y"
        return "5y"
    return "1mo" if sync_days <= 25 else "3mo"


async def _load_existing_indicator_series(
    session: AsyncSession,
    indicator_code: MacroIndicatorCode,
) -> dict[date, Decimal]:
    rows = (
        await session.execute(
            select(MacroIndicator.indicator_date, MacroIndicator.value)
            .where(MacroIndicator.indicator_code == indicator_code.value)
            .order_by(MacroIndicator.indicator_date.asc())
        )
    ).all()
    return {obs_date: Decimal(val) for obs_date, val in rows}


async def run_macro_sync(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mode: Literal["sync", "backfill"] = "sync",
    months: int = 24,
    sync_days: int = 14,
    vcb_days: int = 45,
    advisory_lock_key: int = DEFAULT_MACRO_LOCK_KEY,
    http_client: httpx.AsyncClient | None = None,
    delay_ms: int = 450,
    vcb_delay_ms: int = 350,
    ref_today: date | None = None,
) -> MacroSyncSummary:
    """Sync Yahoo Chart v8 macro series and bounded Vietcombank FX under advisory lock."""
    started = time.monotonic()
    upserted_by_indicator: dict[str, int] = {code.value: 0 for code in MacroIndicatorCode}
    errors: dict[str, str] = {}

    range_str = _yahoo_range_for_run(mode, months=months, sync_days=sync_days)
    vcb_window = (
        max(1, min(vcb_days, MAX_VCB_LOOKBACK_DAYS))
        if mode == "backfill"
        else max(1, min(sync_days, 14))
    )
    max_obs_date = ref_today or vn_today()

    async with PostgresAdvisoryLock(engine, advisory_lock_key) as acquired:
        if not acquired:
            raise RuntimeError(
                f"Macro sync lock {advisory_lock_key} is currently held by another run."
            )

        async def _execute_with_client(client: httpx.AsyncClient) -> None:
            async with session_factory() as session:
                for code in YAHOO_TICKER_MAP:
                    try:
                        pts = await fetch_yahoo_indicator(
                            client,
                            code,
                            range_str=range_str,
                            delay_ms=delay_ms,
                            max_date=max_obs_date,
                        )
                        if pts and mode == "sync":
                            existing_map = await _load_existing_indicator_series(session, code)
                            earliest_fetched = min(pt.indicator_date for pt in pts)
                            prior_dates = [d for d in existing_map if d < earliest_fetched]
                            prior_val = existing_map[max(prior_dates)] if prior_dates else None
                            pts = attach_change_pct_1d(pts, prior_value=prior_val)
                        upserted_by_indicator[code.value] = await upsert_macro_indicators(
                            session, pts
                        )
                    except Exception as exc:
                        logger.warning("Macro sync failed for %s: %s", code.value, exc)
                        errors[code.value] = str(exc)

                try:
                    existing_vcb = await _load_existing_indicator_series(
                        session, MacroIndicatorCode.USD_VND_VCB
                    )
                    vcb_pts = await fetch_vcb_rates_window(
                        client,
                        lookback_days=vcb_window,
                        existing_dates=set(existing_vcb.keys()),
                        ref_today=max_obs_date,
                        delay_ms=vcb_delay_ms,
                    )
                    if vcb_pts:
                        earliest_vcb = min(pt.indicator_date for pt in vcb_pts)
                        prior_dates = [d for d in existing_vcb if d < earliest_vcb]
                        prior_val = existing_vcb[max(prior_dates)] if prior_dates else None
                        vcb_pts = attach_change_pct_1d(vcb_pts, prior_value=prior_val)
                    upserted_by_indicator[MacroIndicatorCode.USD_VND_VCB.value] = (
                        await upsert_macro_indicators(session, vcb_pts)
                    )
                except Exception as exc:
                    logger.warning("Macro sync failed for USD_VND_VCB: %s", exc)
                    errors[MacroIndicatorCode.USD_VND_VCB.value] = str(exc)

                await session.commit()

        if http_client is not None:
            await _execute_with_client(http_client)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=True,
            ) as owned_client:
                await _execute_with_client(owned_client)

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Completed macro sync mode=%s duration_ms=%d upserted=%s errors=%s",
        mode,
        duration_ms,
        upserted_by_indicator,
        errors,
    )
    return MacroSyncSummary(
        mode=mode,
        duration_ms=duration_ms,
        upserted_by_indicator=upserted_by_indicator,
        errors=errors,
    )

