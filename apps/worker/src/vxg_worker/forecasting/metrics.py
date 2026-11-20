"""Walk-forward evaluation metrics, Naive baseline comparisons, tick rounding & confidence."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import numpy as np

from vxg_shared.enums import CommodityCategory, ForecastHorizon, PredictedDirection
from vxg_worker.forecasting.types import BacktestPoint, WalkForwardSummary


def classify_direction(
    delta_vnd: int,
    baseline_vnd: int,
    *,
    domain: CommodityCategory,
    horizon: ForecastHorizon,
    commodity_code: str = "",
) -> PredictedDirection:
    """Map expected/actual VND change onto UP, DOWN, or FLAT using domain noise deadbands."""
    if baseline_vnd <= 0:
        return PredictedDirection.UNKNOWN

    pct = (delta_vnd / baseline_vnd) * 100.0
    if domain == CommodityCategory.GOLD:
        threshold_pct = 0.70 if horizon == ForecastHorizon.T_PLUS_7 else 0.35
        if abs(pct) < threshold_pct:
            return PredictedDirection.FLAT
        return PredictedDirection.UP if delta_vnd > 0 else PredictedDirection.DOWN

    if domain == CommodityCategory.FUEL:
        if abs(delta_vnd) < 80:
            return PredictedDirection.FLAT
        return PredictedDirection.UP if delta_vnd > 0 else PredictedDirection.DOWN

    # Gas
    if "12KG" in commodity_code.upper():
        if abs(delta_vnd) < 2000:
            return PredictedDirection.FLAT
    elif abs(pct) < 0.50:
        return PredictedDirection.FLAT
    return PredictedDirection.UP if delta_vnd > 0 else PredictedDirection.DOWN


def round_price_tick(price_vnd: float, domain: CommodityCategory) -> int:
    """Round continuous VND forecast to Vietnamese market trading ticks."""
    step = 100_000 if domain == CommodityCategory.GOLD else (10 if domain == CommodityCategory.FUEL else 1_000)
    rounded = int(round(price_vnd / step) * step)
    return max(step, rounded)


def build_backtest_point(
    *,
    forecast_date: object,
    target_date: object,
    actual_date: object,
    baseline_price_vnd: int,
    predicted_price_vnd: int,
    lower_bound_vnd: int,
    upper_bound_vnd: int,
    actual_price_vnd: int,
    domain: CommodityCategory,
    horizon: ForecastHorizon,
    commodity_code: str,
    residual_value: float,
) -> BacktestPoint:
    """Construct one BacktestPoint computing error metrics and direction hits cleanly."""
    from datetime import date as _date

    assert isinstance(forecast_date, _date)
    assert isinstance(target_date, _date)
    assert isinstance(actual_date, _date)

    abs_err = abs(predicted_price_vnd - actual_price_vnd)
    pct_err = (Decimal(abs_err) / Decimal(actual_price_vnd) * Decimal("100")).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )
    pred_dir = classify_direction(
        predicted_price_vnd - baseline_price_vnd,
        baseline_price_vnd,
        domain=domain,
        horizon=horizon,
        commodity_code=commodity_code,
    )
    act_dir = classify_direction(
        actual_price_vnd - baseline_price_vnd,
        baseline_price_vnd,
        domain=domain,
        horizon=horizon,
        commodity_code=commodity_code,
    )
    return BacktestPoint(
        forecast_date=forecast_date,
        target_date=target_date,
        actual_date=actual_date,
        baseline_price_vnd=baseline_price_vnd,
        predicted_price_vnd=predicted_price_vnd,
        lower_bound_vnd=lower_bound_vnd,
        upper_bound_vnd=upper_bound_vnd,
        actual_price_vnd=actual_price_vnd,
        abs_error_vnd=abs_err,
        pct_error=pct_err,
        predicted_direction=pred_dir,
        actual_direction=act_dir,
        direction_correct=(pred_dir == act_dir),
        within_interval=(lower_bound_vnd <= actual_price_vnd <= upper_bound_vnd),
        residual_value=residual_value,
    )


def summarize_walk_forward(points: list[BacktestPoint]) -> WalkForwardSummary:
    """Compute MAE, MAPE, Naive comparison, directional accuracy, coverage & error quantiles."""
    if not points:
        return WalkForwardSummary(
            sample_count=0,
            mae_vnd=0,
            mape_pct=0.0,
            naive_mae_vnd=0,
            naive_mape_pct=0.0,
            directional_accuracy_pct=0.0,
            interval_coverage_pct=0.0,
            q10_error=0.0,
            q90_error=0.0,
            points=[],
        )

    abs_errs = np.array([p.abs_error_vnd for p in points], dtype=np.float64)
    pct_errs = np.array([float(p.pct_error) for p in points], dtype=np.float64)
    naive_abs = np.array(
        [abs(p.baseline_price_vnd - p.actual_price_vnd) for p in points],
        dtype=np.float64,
    )
    naive_pct = np.array(
        [abs(p.baseline_price_vnd - p.actual_price_vnd) / p.actual_price_vnd * 100.0 for p in points],
        dtype=np.float64,
    )
    dir_hits = np.array([1.0 if p.direction_correct else 0.0 for p in points], dtype=np.float64)
    cov_hits = np.array([1.0 if p.within_interval else 0.0 for p in points], dtype=np.float64)
    residuals = np.array([p.residual_value for p in points], dtype=np.float64)

    q10, q90 = np.quantile(residuals, [0.10, 0.90])
    return WalkForwardSummary(
        sample_count=len(points),
        mae_vnd=int(round(float(np.mean(abs_errs)))),
        mape_pct=round(float(np.mean(pct_errs)), 3),
        naive_mae_vnd=int(round(float(np.mean(naive_abs)))),
        naive_mape_pct=round(float(np.mean(naive_pct)), 3),
        directional_accuracy_pct=round(float(np.mean(dir_hits) * 100.0), 1),
        interval_coverage_pct=round(float(np.mean(cov_hits) * 100.0), 1),
        q10_error=float(q10),
        q90_error=float(q90),
        points=points,
    )


def compute_confidence_score(
    summary: WalkForwardSummary,
    *,
    baseline_vnd: int,
    lower_vnd: int,
    upper_vnd: int,
    max_cap: float = 0.90,
) -> Decimal:
    """Compute bounded confidence score in [0.15, max_cap] from empirical walk-forward skill."""
    dir_score = max(0.0, min(1.0, summary.directional_accuracy_pct / 100.0))
    if summary.naive_mape_pct > 0.0:
        skill_over_naive = max(0.0, min(1.0, 1.0 - (summary.mape_pct / summary.naive_mape_pct)))
    else:
        skill_over_naive = 0.35

    rel_width = ((upper_vnd - lower_vnd) / baseline_vnd) if baseline_vnd > 0 else 0.15
    tightness_score = max(0.0, min(1.0, 1.0 - (rel_width / 0.12)))
    coverage_score = max(0.0, min(1.0, summary.interval_coverage_pct / 100.0))

    raw = (
        0.40 * dir_score
        + 0.25 * skill_over_naive
        + 0.20 * coverage_score
        + 0.15 * tightness_score
    )
    clamped = max(0.15, min(max_cap, raw))
    return Decimal(str(round(clamped, 3))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

