"""Orchestrator running bounded historical backfills across Gold, Fuel, and Gas."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vxg_worker.history.backfill_fuel import backfill_fuel_history
from vxg_worker.history.backfill_gas import backfill_gas_history
from vxg_worker.history.backfill_gold import backfill_gold_history
from vxg_worker.history.rollup import rollup_daily_from_price_records
from vxg_worker.locks import PostgresAdvisoryLock
from vxg_worker.persistence import load_catalog_lookup

logger = logging.getLogger("vxg_worker.history")

DEFAULT_BACKFILL_LOCK_KEY = 908_712_003


class BackfillDomain(StrEnum):
    ALL = "ALL"
    GOLD = "GOLD"
    FUEL = "FUEL"
    GAS = "GAS"


@dataclass(slots=True)
class BackfillRunSummary:
    """Structured telemetry returned after completing a historical backfill run."""

    domain: str
    months: int
    duration_ms: int
    domain_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    rollup_rows: int = 0


async def run_history_backfill(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    domain: BackfillDomain = BackfillDomain.ALL,
    months: int = 24,
    advisory_lock_key: int = DEFAULT_BACKFILL_LOCK_KEY,
    http_client: httpx.AsyncClient | None = None,
) -> BackfillRunSummary:
    """Execute idempotent historical backfill safely inside a dedicated advisory lock."""
    started = time.monotonic()
    domain_stats: dict[str, dict[str, int]] = {}
    rollup_rows = 0

    async with PostgresAdvisoryLock(engine, advisory_lock_key) as acquired:
        if not acquired:
            raise RuntimeError(
                f"History backfill lock {advisory_lock_key} is currently held by another run."
            )

        async def _execute_with_client(client: httpx.AsyncClient) -> None:
            nonlocal rollup_rows
            async with session_factory() as session:
                catalog = await load_catalog_lookup(session)

                if domain in (BackfillDomain.ALL, BackfillDomain.GOLD):
                    domain_stats["GOLD"] = await backfill_gold_history(
                        session,
                        client,
                        catalog,
                        months=months,
                    )
                if domain in (BackfillDomain.ALL, BackfillDomain.FUEL):
                    domain_stats["FUEL"] = await backfill_fuel_history(
                        session,
                        client,
                        catalog,
                        months=months,
                    )
                if domain in (BackfillDomain.ALL, BackfillDomain.GAS):
                    domain_stats["GAS"] = await backfill_gas_history(
                        session,
                        client,
                        catalog,
                        months=months,
                    )

                rollup_rows = await rollup_daily_from_price_records(session)
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
        "Completed history backfill domain=%s months=%d duration_ms=%d stats=%s rollup=%d",
        domain.value,
        months,
        duration_ms,
        domain_stats,
        rollup_rows,
    )
    return BackfillRunSummary(
        domain=domain.value,
        months=months,
        duration_ms=duration_ms,
        domain_stats=domain_stats,
        rollup_rows=rollup_rows,
    )

