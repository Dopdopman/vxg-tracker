"""Walk-forward backtest persistence and live forecast due-date evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.models import (
    Commodity,
    DailyPriceSeries,
    ForecastEvaluation,
    PriceForecast,
)
from vxg_shared.enums import (
    CommodityCategory,
    ForecastEvaluationType,
    ForecastHorizon,
    ForecastStatus,
    PredictedDirection,
)
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.forecasting.metrics import classify_direction
from vxg_worker.forecasting.types import BacktestPoint


@dataclass(frozen=True, slots=True)
class EvaluationRunSummary:
    """Telemetry returned by evaluate_due_forecasts."""

    as_of_date: date
    due_candidates: int
    evaluated_inserted: int
    waiting_actual_price: int


async def evaluate_due_forecasts(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
) -> EvaluationRunSummary:
    """Evaluate due LIVE READY PriceForecast snapshots against realized `daily_price_series`."""
    ref_date = as_of_date or vn_today()
    already_evaluated_subq = select(ForecastEvaluation.forecast_id).where(
        ForecastEvaluation.forecast_id.is_not(None)
    )

    due_rows = (
        await session.execute(
            select(PriceForecast, Commodity)
            .join(Commodity, PriceForecast.commodity_id == Commodity.id)
            .where(
                PriceForecast.status == ForecastStatus.READY.value,
                PriceForecast.target_date <= ref_date,
                ~PriceForecast.id.in_(already_evaluated_subq),
            )
            .order_by(PriceForecast.target_date.asc())
        )
    ).all()

    if not due_rows:
        return EvaluationRunSummary(
            as_of_date=ref_date,
            due_candidates=0,
            evaluated_inserted=0,
            waiting_actual_price=0,
        )

    c_ids = {int(pf.commodity_id) for pf, _ in due_rows}
    l_ids = {int(pf.location_id) for pf, _ in due_rows}
    min_target = min(pf.target_date for pf, _ in due_rows)
    max_target = max(pf.target_date for pf, _ in due_rows) + timedelta(days=3)

    actual_rows = (
        await session.execute(
            select(
                DailyPriceSeries.commodity_id,
                DailyPriceSeries.location_id,
                DailyPriceSeries.price_date,
                DailyPriceSeries.reference_price_vnd,
            )
            .where(
                DailyPriceSeries.commodity_id.in_(c_ids),
                DailyPriceSeries.location_id.in_(l_ids),
                DailyPriceSeries.price_date >= min_target,
                DailyPriceSeries.price_date <= max_target,
            )
            .order_by(DailyPriceSeries.price_date.asc())
        )
    ).all()

    actual_by_pair: dict[tuple[int, int], list[tuple[date, int]]] = {}
    for cid, lid, p_date, ref_vnd in actual_rows:
        actual_by_pair.setdefault((int(cid), int(lid)), []).append((p_date, int(ref_vnd)))

    now_vn = vn_now()
    to_insert: list[dict[str, Any]] = []
    waiting = 0

    for pf, commodity in due_rows:
        if (
            pf.baseline_price_vnd is None
            or pf.point_estimate_vnd is None
            or pf.predicted_direction not in (
                PredictedDirection.UP.value,
                PredictedDirection.DOWN.value,
                PredictedDirection.FLAT.value,
            )
        ):
            continue

        pair_series = actual_by_pair.get((int(pf.commodity_id), int(pf.location_id)), [])
        match = next(
            (
                (d, val)
                for d, val in pair_series
                if pf.target_date <= d <= pf.target_date + timedelta(days=3)
            ),
            None,
        )
        if match is None:
            waiting += 1
            continue

        actual_date, actual_vnd = match
        abs_err = abs(int(pf.point_estimate_vnd) - actual_vnd)
        pct_err = (Decimal(abs_err) / Decimal(actual_vnd) * Decimal("100")).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
        actual_dir = classify_direction(
            actual_vnd - int(pf.baseline_price_vnd),
            int(pf.baseline_price_vnd),
            domain=CommodityCategory(commodity.category),
            horizon=ForecastHorizon(pf.horizon_type),
            commodity_code=commodity.code,
        )
        within_band = (
            pf.lower_bound_vnd is not None
            and pf.upper_bound_vnd is not None
            and int(pf.lower_bound_vnd) <= actual_vnd <= int(pf.upper_bound_vnd)
        )

        to_insert.append(
            {
                "forecast_id": pf.id,
                "commodity_id": pf.commodity_id,
                "location_id": pf.location_id,
                "horizon_type": pf.horizon_type,
                "model_version": pf.model_version,
                "evaluation_type": ForecastEvaluationType.LIVE.value,
                "forecast_date": pf.forecast_date,
                "target_date": pf.target_date,
                "actual_date": actual_date,
                "baseline_price_vnd": pf.baseline_price_vnd,
                "predicted_price_vnd": pf.point_estimate_vnd,
                "lower_bound_vnd": pf.lower_bound_vnd,
                "upper_bound_vnd": pf.upper_bound_vnd,
                "actual_price_vnd": actual_vnd,
                "abs_error_vnd": abs_err,
                "pct_error": pct_err,
                "predicted_direction": pf.predicted_direction,
                "actual_direction": actual_dir.value,
                "direction_correct": (pf.predicted_direction == actual_dir.value),
                "within_interval": within_band,
                "evaluated_at": now_vn,
            }
        )

    inserted = 0
    if to_insert:
        stmt = (
            pg_insert(ForecastEvaluation)
            .values(to_insert)
            .on_conflict_do_nothing(index_elements=["forecast_id"])
            .returning(ForecastEvaluation.id)
        )
        inserted = len((await session.execute(stmt)).scalars().all())

    return EvaluationRunSummary(
        as_of_date=ref_date,
        due_candidates=len(due_rows),
        evaluated_inserted=inserted,
        waiting_actual_price=waiting,
    )


async def persist_backtest_evaluations(
    session: AsyncSession,
    *,
    commodity_id: int,
    location_id: int,
    horizon: ForecastHorizon,
    model_version: str,
    points: list[BacktestPoint],
) -> int:
    """Upsert Walk-Forward BACKTEST observations clearly isolated from LIVE evaluations."""
    if not points:
        return 0

    now_vn = vn_now()
    deduped: dict[tuple[date, date], BacktestPoint] = {}
    for pt in points:
        deduped[(pt.forecast_date, pt.target_date)] = pt

    rows = [
        {
            "forecast_id": None,
            "commodity_id": commodity_id,
            "location_id": location_id,
            "horizon_type": horizon.value,
            "model_version": model_version,
            "evaluation_type": ForecastEvaluationType.BACKTEST.value,
            "forecast_date": pt.forecast_date,
            "target_date": pt.target_date,
            "actual_date": pt.actual_date,
            "baseline_price_vnd": pt.baseline_price_vnd,
            "predicted_price_vnd": pt.predicted_price_vnd,
            "lower_bound_vnd": pt.lower_bound_vnd,
            "upper_bound_vnd": pt.upper_bound_vnd,
            "actual_price_vnd": pt.actual_price_vnd,
            "abs_error_vnd": pt.abs_error_vnd,
            "pct_error": pt.pct_error,
            "predicted_direction": pt.predicted_direction.value,
            "actual_direction": pt.actual_direction.value,
            "direction_correct": pt.direction_correct,
            "within_interval": pt.within_interval,
            "evaluated_at": now_vn,
        }
        for pt in deduped.values()
    ]

    stmt = pg_insert(ForecastEvaluation).values(rows)
    excluded = stmt.excluded
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=[
            "commodity_id",
            "location_id",
            "forecast_date",
            "target_date",
            "horizon_type",
            "model_version",
        ],
        index_where=text("evaluation_type = 'BACKTEST'"),
        set_={
            "actual_date": excluded.actual_date,
            "baseline_price_vnd": excluded.baseline_price_vnd,
            "predicted_price_vnd": excluded.predicted_price_vnd,
            "lower_bound_vnd": excluded.lower_bound_vnd,
            "upper_bound_vnd": excluded.upper_bound_vnd,
            "actual_price_vnd": excluded.actual_price_vnd,
            "abs_error_vnd": excluded.abs_error_vnd,
            "pct_error": excluded.pct_error,
            "predicted_direction": excluded.predicted_direction,
            "actual_direction": excluded.actual_direction,
            "direction_correct": excluded.direction_correct,
            "within_interval": excluded.within_interval,
            "evaluated_at": excluded.evaluated_at,
        },
    )
    await session.execute(upsert_stmt)
    return len(rows)

