"""Fuel adjustment cycle forecasting engine (Thursday 15:00 UTC+7) aligned with MOIT 7d cycle averages."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from vxg_shared.enums import (
    CommodityCategory,
    ForecastHorizon,
    ForecastStatus,
    MacroIndicatorCode,
    PredictedDirection,
)
from vxg_shared.timezone import VN_TZ_NAME
from vxg_worker.forecasting.calendar import next_fuel_adjustment_date
from vxg_worker.forecasting.features import (
    collapse_fuel_adjustment_cycles,
    nearest_prior_value,
    rolling_mean_pct_change,
    rolling_window_mean,
    slice_macro_as_of,
    slice_prices_as_of,
)
from vxg_worker.forecasting.metrics import (
    build_backtest_point,
    classify_direction,
    compute_confidence_score,
    round_price_tick,
    summarize_walk_forward,
)
from vxg_worker.forecasting.news_overlay import apply_news_risk_overlay
from vxg_worker.forecasting.types import (
    BacktestPoint,
    ForecastOutput,
    MacroSeriesBundle,
    NewsObs,
    PriceObs,
)

FUEL_MODEL_VERSION = "fuel_ridge_cycle_v2"
FUEL_MIN_CYCLES = 12
_FUEL_WALK_FORWARD_MIN_TRAIN = 8

_FUEL_FEATURE_SPECS: tuple[tuple[str, str], ...] = (
    ("brent_cycle_7d_chg_pct", "Dầu thô Brent TB kỳ hiện tại so với kỳ trước (%)"),
    ("wti_cycle_7d_chg_pct", "Dầu thô WTI TB kỳ hiện tại so với kỳ trước (%)"),
    ("fx_cycle_7d_chg_pct", "Tỷ giá USD/VND TB kỳ hiện tại so với kỳ trước (%)"),
    ("import_proxy_cycle_chg_pct", "Chi phí nhập khẩu quy đổi Brent×USD trong kỳ (%)"),
    ("prev_cycle_delta_vnd", "Mức điều chỉnh kỳ gần nhất (VND/lít)"),
)


@dataclass(frozen=True, slots=True)
class _FuelSupervisedSample:
    forecast_date: date
    target_date: date
    baseline_vnd: int
    actual_vnd: int
    delta_vnd: float
    features: list[float]


def _cycle_avg_pct_change(
    date_map: dict[date, Decimal],
    *,
    fc_date: date,
    curr_cycle_date: date,
    prev_cycle_date: date,
    max_window_days: int = 7,
) -> float | None:
    """Compare current-cycle mean (<= fc_date) against prior-cycle mean (Zero Look-Ahead)."""
    curr_floor = max(curr_cycle_date, fc_date - timedelta(days=max_window_days))
    curr_vals = [float(v) for d, v in date_map.items() if curr_floor < d <= fc_date and v > 0]
    if not curr_vals:
        return rolling_mean_pct_change(date_map, fc_date, window_days=max_window_days)

    curr_mean = sum(curr_vals) / len(curr_vals)
    prev_floor = max(prev_cycle_date, curr_cycle_date - timedelta(days=max_window_days))
    prev_vals = [
        float(v) for d, v in date_map.items() if prev_floor < d <= curr_cycle_date and v > 0
    ]
    prev_mean = (
        (sum(prev_vals) / len(prev_vals))
        if prev_vals
        else rolling_window_mean(date_map, curr_cycle_date, window_days=max_window_days)
    )
    if prev_mean is None or prev_mean <= 0:
        return None
    return ((curr_mean - prev_mean) / prev_mean) * 100.0


def _build_fuel_features(
    cycles: list[PriceObs],
    idx: int,
    macro_bundle: MacroSeriesBundle,
    *,
    fc_date: date,
) -> list[float]:
    curr_d = cycles[idx].price_date
    prev_d = cycles[idx - 1].price_date if idx >= 1 else (curr_d - timedelta(days=7))
    brent_map = macro_bundle.by_code.get(MacroIndicatorCode.BRENT_USD.value, {})
    wti_map = macro_bundle.by_code.get(MacroIndicatorCode.WTI_USD.value, {})
    fx_vcb = macro_bundle.by_code.get(MacroIndicatorCode.USD_VND_VCB.value, {})
    fx_mkt = macro_bundle.by_code.get(MacroIndicatorCode.USD_VND_MARKET.value, {})
    fx_map = fx_vcb if nearest_prior_value(fx_vcb, fc_date, max_lookback_days=7) else fx_mkt

    brent_chg = _cycle_avg_pct_change(
        brent_map,
        fc_date=fc_date,
        curr_cycle_date=curr_d,
        prev_cycle_date=prev_d,
        max_window_days=7,
    )
    wti_chg = _cycle_avg_pct_change(
        wti_map,
        fc_date=fc_date,
        curr_cycle_date=curr_d,
        prev_cycle_date=prev_d,
        max_window_days=7,
    )
    fx_chg = _cycle_avg_pct_change(
        fx_map,
        fc_date=fc_date,
        curr_cycle_date=curr_d,
        prev_cycle_date=prev_d,
        max_window_days=7,
    )
    if brent_chg is not None and fx_chg is not None:
        import_proxy: float | None = brent_chg + fx_chg
    elif wti_chg is not None and fx_chg is not None:
        import_proxy = wti_chg + fx_chg
    else:
        import_proxy = brent_chg if brent_chg is not None else wti_chg

    prev_delta = (
        float(cycles[idx].reference_price_vnd - cycles[idx - 1].reference_price_vnd)
        if idx >= 1
        else 0.0
    )

    def _safe(v: float | None) -> float:
        return v if v is not None and math.isfinite(v) else float("nan")

    return [
        _safe(brent_chg),
        _safe(wti_chg),
        _safe(fx_chg),
        _safe(import_proxy),
        prev_delta,
    ]


def _fit_fuel_pipeline(samples: list[_FuelSupervisedSample]) -> tuple[Pipeline, str]:
    x_mat = np.array([s.features for s in samples], dtype=np.float64)
    y_vec = np.array([s.delta_vnd for s in samples], dtype=np.float64)

    with warnings.catch_warnings():
        warnings.filterwarnings("error", category=ConvergenceWarning)
        try:
            pipe = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                    ("scaler", StandardScaler()),
                    ("reg", Ridge(alpha=3.0)),
                ]
            )
            pipe.fit(x_mat, y_vec)
            return pipe, "ridge_cycle"
        except Exception:
            huber_pipe = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                    ("scaler", StandardScaler()),
                    ("reg", HuberRegressor(alpha=0.5, epsilon=1.35, max_iter=500)),
                ]
            )
            huber_pipe.fit(x_mat, y_vec)
            return huber_pipe, "huber_fallback"


def _predict_fuel_delta(pipe: Pipeline, feat: list[float], baseline_vnd: int) -> float:
    raw = float(pipe.predict(np.array([feat], dtype=np.float64))[0])
    if not math.isfinite(raw):
        raw = 0.0
    max_step = max(3_000.0, baseline_vnd * 0.15)
    return max(-max_step, min(max_step, raw))


def generate_fuel_forecast(
    *,
    commodity_code: str,
    location_code: str,
    as_of_dt_vn: datetime,
    price_series: list[PriceObs],
    macro_bundle: MacroSeriesBundle,
    news_items: list[NewsObs],
) -> ForecastOutput:
    """Generate zero-look-ahead Fuel adjustment forecast for Thursday 15:00 VN."""
    as_of_date = as_of_dt_vn.date()
    sliced_prices = slice_prices_as_of(price_series, as_of_date)
    cycles = collapse_fuel_adjustment_cycles(sliced_prices)
    sliced_macro = slice_macro_as_of(macro_bundle, as_of_date)
    target_date = next_fuel_adjustment_date(as_of_dt_vn)

    if len(cycles) < FUEL_MIN_CYCLES:
        baseline = cycles[-1].reference_price_vnd if cycles else None
        reason = (
            f"Hiện có {len(cycles)}/{FUEL_MIN_CYCLES} kỳ điều hành thật; "
            f"dự báo sẽ tự động kích hoạt khi tích lũy đủ {FUEL_MIN_CYCLES} kỳ."
        )
        return ForecastOutput(
            commodity_code=commodity_code,
            location_code=location_code,
            forecast_date=as_of_date,
            target_date=target_date,
            horizon=ForecastHorizon.NEXT_FUEL_CYCLE,
            model_version=FUEL_MODEL_VERSION,
            status=ForecastStatus.INSUFFICIENT_DATA,
            baseline_price_vnd=baseline,
            point_estimate_vnd=None,
            lower_bound_vnd=None,
            upper_bound_vnd=None,
            expected_change_vnd=None,
            expected_change_pct=None,
            predicted_direction=PredictedDirection.UNKNOWN,
            confidence_score=None,
            news_adjustment_pct=Decimal("0.000"),
            insufficient_reason=reason,
            drivers_json={
                "cycle_count": len(cycles),
                "required_min_cycles": FUEL_MIN_CYCLES,
                "feature_cutoff_vn": as_of_dt_vn.isoformat(),
                "timezone": VN_TZ_NAME,
            },
        )

    samples: list[_FuelSupervisedSample] = []
    for k in range(1, len(cycles) - 1):
        curr = cycles[k]
        nxt = cycles[k + 1]
        if nxt.price_date > as_of_date or (nxt.price_date - curr.price_date).days < 2:
            continue
        fc_date = min(nxt.price_date - timedelta(days=1), curr.price_date + timedelta(days=6))
        samples.append(
            _FuelSupervisedSample(
                forecast_date=fc_date,
                target_date=nxt.price_date,
                baseline_vnd=curr.reference_price_vnd,
                actual_vnd=nxt.reference_price_vnd,
                delta_vnd=float(nxt.reference_price_vnd - curr.reference_price_vnd),
                features=_build_fuel_features(cycles, k, sliced_macro, fc_date=fc_date),
            )
        )

    if len(samples) < 2:
        baseline = cycles[-1].reference_price_vnd if cycles else None
        return ForecastOutput(
            commodity_code=commodity_code,
            location_code=location_code,
            forecast_date=as_of_date,
            target_date=target_date,
            horizon=ForecastHorizon.NEXT_FUEL_CYCLE,
            model_version=FUEL_MODEL_VERSION,
            status=ForecastStatus.INSUFFICIENT_DATA,
            baseline_price_vnd=baseline,
            point_estimate_vnd=None,
            lower_bound_vnd=None,
            upper_bound_vnd=None,
            expected_change_vnd=None,
            expected_change_pct=None,
            predicted_direction=PredictedDirection.UNKNOWN,
            confidence_score=None,
            news_adjustment_pct=Decimal("0.000"),
            insufficient_reason=(
                f"Chỉ có {len(samples)} chu kỳ hợp lệ >= 2 ngày để huấn luyện mô hình Xăng dầu."
            ),
            drivers_json={
                "cycle_count": len(cycles),
                "valid_sample_count": len(samples),
                "required_min_cycles": FUEL_MIN_CYCLES,
                "feature_cutoff_vn": as_of_dt_vn.isoformat(),
                "timezone": VN_TZ_NAME,
            },
        )

    bt_points: list[BacktestPoint] = []
    prior_residuals: list[float] = []

    for k in range(_FUEL_WALK_FORWARD_MIN_TRAIN, len(samples)):
        test_sample = samples[k]
        train_slice = samples[:k]
        pipe, _ = _fit_fuel_pipeline(train_slice)
        pred_delta = _predict_fuel_delta(pipe, test_sample.features, test_sample.baseline_vnd)

        if len(prior_residuals) >= 6:
            q_low, q_high = np.quantile(np.array(prior_residuals, dtype=np.float64), [0.10, 0.90])
        else:
            std_y = float(np.std(np.array([s.delta_vnd for s in train_slice], dtype=np.float64)))
            half_w = max(250.0, 1.28 * std_y)
            q_low, q_high = -half_w, half_w

        q_low_v = min(float(q_low), -120.0)
        q_high_v = max(float(q_high), 120.0)

        pred_vnd = round_price_tick(test_sample.baseline_vnd + pred_delta, CommodityCategory.FUEL)
        low_vnd = round_price_tick(test_sample.baseline_vnd + pred_delta + q_low_v, CommodityCategory.FUEL)
        high_vnd = round_price_tick(test_sample.baseline_vnd + pred_delta + q_high_v, CommodityCategory.FUEL)
        low_vnd = min(low_vnd, pred_vnd)
        high_vnd = max(high_vnd, pred_vnd)

        residual = test_sample.delta_vnd - pred_delta
        prior_residuals.append(residual)
        bt_points.append(
            build_backtest_point(
                forecast_date=test_sample.forecast_date,
                target_date=test_sample.target_date,
                actual_date=test_sample.target_date,
                baseline_price_vnd=test_sample.baseline_vnd,
                predicted_price_vnd=pred_vnd,
                lower_bound_vnd=low_vnd,
                upper_bound_vnd=high_vnd,
                actual_price_vnd=test_sample.actual_vnd,
                domain=CommodityCategory.FUEL,
                horizon=ForecastHorizon.NEXT_FUEL_CYCLE,
                commodity_code=commodity_code,
                residual_value=residual,
            )
        )

    summary = summarize_walk_forward(bt_points)
    final_pipe, estimator_used = _fit_fuel_pipeline(samples)

    latest_cycle_idx = len(cycles) - 1
    latest_cycle = cycles[latest_cycle_idx]
    baseline_vnd = latest_cycle.reference_price_vnd
    live_feat = _build_fuel_features(cycles, latest_cycle_idx, sliced_macro, fc_date=as_of_date)
    live_delta = _predict_fuel_delta(final_pipe, live_feat, baseline_vnd)

    q10 = min(summary.q10_error, -150.0) if summary.sample_count >= 6 else -400.0
    q90 = max(summary.q90_error, 150.0) if summary.sample_count >= 6 else 400.0

    point_vnd = round_price_tick(baseline_vnd + live_delta, CommodityCategory.FUEL)
    lower_vnd = round_price_tick(baseline_vnd + live_delta + q10, CommodityCategory.FUEL)
    upper_vnd = round_price_tick(baseline_vnd + live_delta + q90, CommodityCategory.FUEL)
    lower_vnd = min(lower_vnd, point_vnd)
    upper_vnd = max(upper_vnd, point_vnd)
    if lower_vnd == point_vnd:
        lower_vnd = max(100, point_vnd - 100)
    if upper_vnd == point_vnd:
        upper_vnd = point_vnd + 100

    pred_dir = classify_direction(
        point_vnd - baseline_vnd,
        baseline_vnd,
        domain=CommodityCategory.FUEL,
        horizon=ForecastHorizon.NEXT_FUEL_CYCLE,
        commodity_code=commodity_code,
    )
    base_conf = compute_confidence_score(
        summary,
        baseline_vnd=baseline_vnd,
        lower_vnd=lower_vnd,
        upper_vnd=upper_vnd,
    )
    overlay = apply_news_risk_overlay(
        domain=CommodityCategory.FUEL,
        point_estimate_vnd=point_vnd,
        lower_bound_vnd=lower_vnd,
        upper_bound_vnd=upper_vnd,
        predicted_direction=pred_dir,
        base_confidence=base_conf,
        news_items=news_items,
        cutoff_dt_vn=as_of_dt_vn,
    )

    reg_step = final_pipe.named_steps["reg"]
    coefs = [float(c) for c in reg_step.coef_]
    abs_sum = sum(abs(c) for c in coefs) or 1.0
    drivers_list = sorted(
        [
            {
                "key": key,
                "label_vi": label_vi,
                "value": None if math.isnan(val) else round(val, 2),
                "weight_pct": round(abs(coef) / abs_sum * 100.0, 1),
                "direction": "UP" if coef > 0 else ("DOWN" if coef < 0 else "FLAT"),
            }
            for (key, label_vi), val, coef in zip(_FUEL_FEATURE_SPECS, live_feat, coefs, strict=True)
        ],
        key=lambda d: float(d["weight_pct"]),  # type: ignore[arg-type]
        reverse=True,
    )

    exp_delta = point_vnd - baseline_vnd
    exp_pct = (Decimal(exp_delta) / Decimal(baseline_vnd) * Decimal("100")).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )

    return ForecastOutput(
        commodity_code=commodity_code,
        location_code=location_code,
        forecast_date=as_of_date,
        target_date=target_date,
        horizon=ForecastHorizon.NEXT_FUEL_CYCLE,
        model_version=FUEL_MODEL_VERSION,
        status=ForecastStatus.READY,
        baseline_price_vnd=baseline_vnd,
        point_estimate_vnd=point_vnd,
        lower_bound_vnd=overlay.lower_bound_vnd,
        upper_bound_vnd=overlay.upper_bound_vnd,
        expected_change_vnd=exp_delta,
        expected_change_pct=exp_pct,
        predicted_direction=pred_dir,
        confidence_score=overlay.confidence_score,
        news_adjustment_pct=Decimal("0.000"),
        insufficient_reason=None,
        drivers_json={
            "model_family": f"Ridge ({estimator_used}, MOIT 7d cycle-aligned)",
            "training_cycles": len(cycles),
            "target_schedule": "Kỳ điều hành 15:00 Thứ Năm (UTC+7)",
            "feature_cutoff_vn": as_of_dt_vn.isoformat(),
            "last_actual_date": latest_cycle.price_date.isoformat(),
            "high_volatility_flag": overlay.high_volatility_flag,
            "band_widening_pct": overlay.band_widening_pct,
            "feature_drivers": drivers_list,
            "news_signals": overlay.top_news_payload,
            "backtest": {
                "sample_count": summary.sample_count,
                "mae_vnd": summary.mae_vnd,
                "mape_pct": summary.mape_pct,
                "naive_mae_vnd": summary.naive_mae_vnd,
                "naive_mape_pct": summary.naive_mape_pct,
                "directional_accuracy_pct": summary.directional_accuracy_pct,
                "interval_coverage_pct": summary.interval_coverage_pct,
            },
        },
        backtest_points=bt_points,
    )

