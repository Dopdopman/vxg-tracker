"""End-to-end forecast generation, backtest orchestrator, and ForecastRun DB queue poller."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vxg_shared.db.models import (
    DailyPriceSeries,
    ForecastRun,
    MacroIndicator,
    MarketNewsSignal,
    PriceForecast,
)
from vxg_shared.enums import (
    CommodityCategory,
    CrawlRunStatus,
    CrawlScope,
    ForecastHorizon,
    ForecastStatus,
    FuelZoneCode,
    TriggerType,
)
from vxg_shared.timezone import VN_TZ, to_vn_tz, vn_now
from vxg_worker.forecasting.evaluator import evaluate_due_forecasts, persist_backtest_evaluations
from vxg_worker.forecasting.fuel_model import generate_fuel_forecast
from vxg_worker.forecasting.gas_model import generate_gas_forecast
from vxg_worker.forecasting.gold_model import generate_gold_forecast
from vxg_worker.forecasting.types import (
    ForecastOutput,
    MacroSeriesBundle,
    NewsObs,
    PriceObs,
)
from vxg_worker.locks import PostgresAdvisoryLock
from vxg_worker.persistence import load_catalog_lookup

logger = logging.getLogger("vxg_worker.forecasting")

DEFAULT_FORECAST_LOCK_KEY = 908_712_007

_CITY_LOCATION_CODES: tuple[str, ...] = ("HN", "DN", "HCM")
_FUEL_ZONE_CODES: tuple[str, ...] = (FuelZoneCode.VUNG_1.value, FuelZoneCode.VUNG_2.value)


@dataclass(slots=True)
class ForecastPipelineSummary:
    """Telemetry summary returned after executing forecast generation & optional backtests."""

    scope: str
    as_of_date: date
    duration_ms: int
    total_snapshots: int
    ready_count: int
    insufficient_count: int
    evaluations_inserted: int
    backtest_rows_upserted: int
    outputs: list[ForecastOutput] = field(default_factory=list)


def _domains_for_scope(scope: CrawlScope) -> tuple[CommodityCategory, ...]:
    if scope == CrawlScope.ALL:
        return (CommodityCategory.GOLD, CommodityCategory.FUEL, CommodityCategory.GAS)
    return (CommodityCategory(scope.value),)


async def _load_inputs_strictly_as_of(
    session: AsyncSession,
    *,
    as_of_dt_vn: datetime,
) -> tuple[
    dict[tuple[int, int], list[PriceObs]],
    MacroSeriesBundle,
    list[NewsObs],
]:
    cutoff_date = as_of_dt_vn.date()

    price_rows = (
        await session.execute(
            select(DailyPriceSeries)
            .where(DailyPriceSeries.price_date <= cutoff_date)
            .order_by(
                DailyPriceSeries.commodity_id.asc(),
                DailyPriceSeries.location_id.asc(),
                DailyPriceSeries.price_date.asc(),
            )
        )
    ).scalars().all()

    series_by_pair: dict[tuple[int, int], list[PriceObs]] = {}
    for r in price_rows:
        series_by_pair.setdefault((int(r.commodity_id), int(r.location_id)), []).append(
            PriceObs(
                price_date=r.price_date,
                reference_price_vnd=int(r.reference_price_vnd),
                is_adjustment_day=bool(r.is_adjustment_day),
                series_origin=r.series_origin,
                source_code=r.source_code,
            )
        )

    macro_rows = (
        await session.execute(
            select(MacroIndicator)
            .where(MacroIndicator.indicator_date <= cutoff_date)
            .order_by(MacroIndicator.indicator_code.asc(), MacroIndicator.indicator_date.asc())
        )
    ).all()
    by_code: dict[str, dict[date, Decimal]] = {}
    for (m_row,) in macro_rows:
        by_code.setdefault(m_row.indicator_code, {})[m_row.indicator_date] = Decimal(m_row.value)

    news_floor = as_of_dt_vn - timedelta(hours=336)
    news_rows = (
        await session.execute(
            select(MarketNewsSignal)
            .where(
                MarketNewsSignal.published_at >= news_floor,
                MarketNewsSignal.published_at <= as_of_dt_vn,
            )
            .order_by(MarketNewsSignal.published_at.desc())
        )
    ).scalars().all()
    news_obs = [
        NewsObs(
            id=str(n.id),
            domain=n.domain,
            publisher=n.publisher,
            title=n.title,
            url=n.url,
            published_at_vn=to_vn_tz(n.published_at),
            polarity_score=Decimal(n.polarity_score),
            impact_level=n.impact_level,
            catalysts=[str(c) for c in (n.catalysts or [])],
            summary=n.llm_summary_vi or n.summary_snippet,
        )
        for n in news_rows
    ]
    return series_by_pair, MacroSeriesBundle(by_code=by_code), news_obs


async def _upsert_price_forecasts(
    session: AsyncSession,
    records: list[tuple[int, int, ForecastOutput]],
) -> int:
    if not records:
        return 0

    now_vn = vn_now()
    rows: list[dict[str, Any]] = []
    for commodity_id, location_id, out in records:
        rows.append(
            {
                "commodity_id": commodity_id,
                "location_id": location_id,
                "forecast_date": out.forecast_date,
                "target_date": out.target_date,
                "horizon_type": out.horizon.value,
                "model_version": out.model_version,
                "status": out.status.value,
                "baseline_price_vnd": out.baseline_price_vnd,
                "point_estimate_vnd": out.point_estimate_vnd,
                "lower_bound_vnd": out.lower_bound_vnd,
                "upper_bound_vnd": out.upper_bound_vnd,
                "expected_change_vnd": out.expected_change_vnd,
                "expected_change_pct": out.expected_change_pct,
                "predicted_direction": out.predicted_direction.value,
                "confidence_score": out.confidence_score,
                "news_adjustment_pct": out.news_adjustment_pct,
                "insufficient_reason": out.insufficient_reason,
                "drivers_json": out.drivers_json,
                "created_at": now_vn,
            }
        )

    stmt = pg_insert(PriceForecast).values(rows)
    excluded = stmt.excluded
    upsert_stmt = stmt.on_conflict_do_update(
        constraint="uq_price_forecasts_snapshot",
        set_={
            "target_date": excluded.target_date,
            "status": excluded.status,
            "baseline_price_vnd": excluded.baseline_price_vnd,
            "point_estimate_vnd": excluded.point_estimate_vnd,
            "lower_bound_vnd": excluded.lower_bound_vnd,
            "upper_bound_vnd": excluded.upper_bound_vnd,
            "expected_change_vnd": excluded.expected_change_vnd,
            "expected_change_pct": excluded.expected_change_pct,
            "predicted_direction": excluded.predicted_direction,
            "confidence_score": excluded.confidence_score,
            "news_adjustment_pct": excluded.news_adjustment_pct,
            "insufficient_reason": excluded.insufficient_reason,
            "drivers_json": excluded.drivers_json,
            "created_at": excluded.created_at,
        },
    )
    await session.execute(upsert_stmt)
    return len(rows)


async def run_forecast_pipeline(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    scope: CrawlScope = CrawlScope.ALL,
    as_of_date: date | None = None,
    existing_run_id: uuid.UUID | None = None,
    persist_backtest: bool = False,
    run_evaluation_first: bool = False,
    trigger_type: TriggerType = TriggerType.MANUAL_ADMIN,
    advisory_lock_key: int = DEFAULT_FORECAST_LOCK_KEY,
) -> ForecastPipelineSummary:
    """Execute forecast generation, optional due-date evaluation & backtest persistence under lock."""
    started_mono = time.monotonic()
    now_vn = vn_now()
    if as_of_date is not None and as_of_date != now_vn.date():
        as_of_dt_vn = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, tzinfo=VN_TZ)
    else:
        as_of_dt_vn = now_vn
    effective_date = as_of_dt_vn.date()

    async with PostgresAdvisoryLock(engine, advisory_lock_key) as acquired:
        if not acquired:
            raise RuntimeError(
                f"Forecast lock {advisory_lock_key} is currently held by another run."
            )

        if existing_run_id is not None:
            async with session_factory() as run_sess:
                run_obj = (
                    await run_sess.execute(select(ForecastRun).where(ForecastRun.id == existing_run_id))
                ).scalar_one_or_none()
                if run_obj is not None:
                    run_obj.status = CrawlRunStatus.RUNNING.value
                    run_obj.trigger_type = trigger_type.value
                    await run_sess.commit()

        try:
            async with session_factory() as session:
                evaluations_inserted = 0
                if run_evaluation_first:
                    eval_res = await evaluate_due_forecasts(session, as_of_date=effective_date)
                    evaluations_inserted = eval_res.evaluated_inserted

                catalog = await load_catalog_lookup(session)
                series_by_pair, macro_bundle, news_obs = await _load_inputs_strictly_as_of(
                    session,
                    as_of_dt_vn=as_of_dt_vn,
                )

                target_domains = _domains_for_scope(scope)
                active_commodities = sorted(
                    (c for c in catalog.commodities_by_code.values() if c.is_active),
                    key=lambda c: (c.category, c.display_order, c.code),
                )

                generated_pairs: list[tuple[int, int, ForecastOutput]] = []
                backtest_rows_upserted = 0

                for commodity in active_commodities:
                    domain = CommodityCategory(commodity.category)
                    if domain not in target_domains:
                        continue

                    loc_codes = (
                        _FUEL_ZONE_CODES
                        if domain == CommodityCategory.FUEL
                        else _CITY_LOCATION_CODES
                    )
                    for loc_code in loc_codes:
                        loc = catalog.locations_by_code.get(loc_code)
                        if loc is None:
                            continue
                        pair_series = series_by_pair.get((int(commodity.id), int(loc.id)), [])

                        if domain == CommodityCategory.GOLD:
                            for horizon in (ForecastHorizon.T_PLUS_1, ForecastHorizon.T_PLUS_7):
                                out = generate_gold_forecast(
                                    commodity_code=commodity.code,
                                    location_code=loc.code,
                                    horizon=horizon,
                                    as_of_dt_vn=as_of_dt_vn,
                                    price_series=pair_series,
                                    macro_bundle=macro_bundle,
                                    news_items=news_obs,
                                )
                                generated_pairs.append((int(commodity.id), int(loc.id), out))
                                if persist_backtest and out.backtest_points:
                                    backtest_rows_upserted += await persist_backtest_evaluations(
                                        session,
                                        commodity_id=int(commodity.id),
                                        location_id=int(loc.id),
                                        horizon=out.horizon,
                                        model_version=out.model_version,
                                        points=out.backtest_points,
                                    )
                        elif domain == CommodityCategory.FUEL:
                            out = generate_fuel_forecast(
                                commodity_code=commodity.code,
                                location_code=loc.code,
                                as_of_dt_vn=as_of_dt_vn,
                                price_series=pair_series,
                                macro_bundle=macro_bundle,
                                news_items=news_obs,
                            )
                            generated_pairs.append((int(commodity.id), int(loc.id), out))
                            if persist_backtest and out.backtest_points:
                                backtest_rows_upserted += await persist_backtest_evaluations(
                                    session,
                                    commodity_id=int(commodity.id),
                                    location_id=int(loc.id),
                                    horizon=out.horizon,
                                    model_version=out.model_version,
                                    points=out.backtest_points,
                                )
                        else:
                            out = generate_gas_forecast(
                                commodity_code=commodity.code,
                                location_code=loc.code,
                                as_of_dt_vn=as_of_dt_vn,
                                price_series=pair_series,
                                macro_bundle=macro_bundle,
                                news_items=news_obs,
                            )
                            generated_pairs.append((int(commodity.id), int(loc.id), out))
                            if persist_backtest and out.backtest_points:
                                backtest_rows_upserted += await persist_backtest_evaluations(
                                    session,
                                    commodity_id=int(commodity.id),
                                    location_id=int(loc.id),
                                    horizon=out.horizon,
                                    model_version=out.model_version,
                                    points=out.backtest_points,
                                )

                await _upsert_price_forecasts(session, generated_pairs)
                ready_count = sum(1 for _, _, o in generated_pairs if o.status == ForecastStatus.READY)
                insuff_count = sum(
                    1 for _, _, o in generated_pairs if o.status == ForecastStatus.INSUFFICIENT_DATA
                )

                duration_ms = int((time.monotonic() - started_mono) * 1000)
                if existing_run_id is not None:
                    run_obj = (
                        await session.execute(select(ForecastRun).where(ForecastRun.id == existing_run_id))
                    ).scalar_one_or_none()
                    if run_obj is not None:
                        run_obj.status = CrawlRunStatus.COMPLETED.value
                        run_obj.finished_at = vn_now()
                        run_obj.duration_ms = duration_ms
                        run_obj.forecasts_generated = ready_count
                        run_obj.evaluations_created = evaluations_inserted
                        run_obj.error_summary = None

                await session.commit()

            outputs = [o for _, _, o in generated_pairs]
            logger.info(
                "Forecast pipeline completed: scope=%s as_of=%s total=%d ready=%d insufficient=%d eval=%d backtest=%d duration_ms=%d",
                scope.value,
                effective_date.isoformat(),
                len(outputs),
                ready_count,
                insuff_count,
                evaluations_inserted,
                backtest_rows_upserted,
                duration_ms,
            )
            return ForecastPipelineSummary(
                scope=scope.value,
                as_of_date=effective_date,
                duration_ms=duration_ms,
                total_snapshots=len(outputs),
                ready_count=ready_count,
                insufficient_count=insuff_count,
                evaluations_inserted=evaluations_inserted,
                backtest_rows_upserted=backtest_rows_upserted,
                outputs=outputs,
            )
        except Exception as exc:
            if existing_run_id is not None:
                async with session_factory() as err_sess:
                    run_obj = (
                        await err_sess.execute(select(ForecastRun).where(ForecastRun.id == existing_run_id))
                    ).scalar_one_or_none()
                    if run_obj is not None:
                        run_obj.status = CrawlRunStatus.FAILED.value
                        run_obj.finished_at = vn_now()
                        run_obj.duration_ms = int((time.monotonic() - started_mono) * 1000)
                        run_obj.error_summary = str(exc)[:2000]
                        await err_sess.commit()
            raise


async def poll_queued_forecast_run(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> ForecastPipelineSummary | None:
    """Pick oldest QUEUED ForecastRun safely and execute forecast + evaluation pipeline."""
    async with session_factory() as session:
        queued = (
            await session.execute(
                select(ForecastRun)
                .where(ForecastRun.status == CrawlRunStatus.QUEUED.value)
                .order_by(ForecastRun.started_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if queued is None:
            return None
        run_id = queued.id
        scope = CrawlScope(queued.scope_category)
        trigger = TriggerType(queued.trigger_type)

    return await run_forecast_pipeline(
        engine,
        session_factory,
        scope=scope,
        existing_run_id=run_id,
        persist_backtest=False,
        run_evaluation_first=True,
        trigger_type=trigger,
    )

