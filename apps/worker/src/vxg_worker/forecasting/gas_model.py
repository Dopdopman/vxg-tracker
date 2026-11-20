"""Gas monthly forecasting engine (1st of next month) with strict monthly cycle collapsing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
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
from vxg_worker.forecasting.calendar import next_gas_month_date
from vxg_worker.forecasting.features import (
    collapse_gas_monthly_cycles,
    nearest_prior_value,
    rolling_mean_pct_change,
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

GAS_PASSTHROUGH_VERSION = "gas_macro_passthrough_v1"
GAS_RIDGE_VERSION = "gas_ridge_v1"
GAS_MIN_MONTHS = 6
GAS_RIDGE_MIN_MONTHS = 12
_PASSTHROUGH_MIN_TRAIN = 3
_RIDGE_MIN_TRAIN = 8

_GAS_FEATURE_SPECS: tuple[tuple[str, str], ...] = (
    ("brent_30d_chg_pct", "Dầu thô Brent bình quân 30 ngày (%)"),
    ("wti_30d_chg_pct", "Dầu thô WTI bình quân 30 ngày (%)"),
    ("fx_30d_chg_pct", "Tỷ giá USD/VND bình quân 30 ngày (%)"),
    ("season_sin", "Tính mùa vụ LPG (sin tháng)"),
    ("season_cos", "Tính mùa vụ LPG (cos tháng)"),
    ("prev_month_chg_pct", "Biến động kỳ tháng gần nhất (%)"),
)


@dataclass(frozen=True, slots=True)
class _GasMonthlySample:
    forecast_date: date
    target_date: date
    baseline_vnd: int
    actual_vnd: int
    actual_pct: float
    features: list[float]


def _extract_gas_features(
    monthly: list[PriceObs],
    idx: int,
    macro_bundle: MacroSeriesBundle,
) -> list[float]:
    d = monthly[idx].price_date
    brent_map = macro_bundle.by_code.get(MacroIndicatorCode.BRENT_USD.value, {})
    wti_map = macro_bundle.by_code.get(MacroIndicatorCode.WTI_USD.value, {})
    fx_vcb = macro_bundle.by_code.get(MacroIndicatorCode.USD_VND_VCB.value, {})
    fx_mkt = macro_bundle.by_code.get(MacroIndicatorCode.USD_VND_MARKET.value, {})
    fx_map = fx_vcb if nearest_prior_value(fx_vcb, d, max_lookback_days=7) else fx_mkt

    brent_30 = rolling_mean_pct_change(brent_map, d, window_days=30)
    wti_30 = rolling_mean_pct_change(wti_map, d, window_days=30)
    fx_30 = rolling_mean_pct_change(fx_map, d, window_days=30)

    angle = 2.0 * math.pi * float(d.month) / 12.0
    prev_pct = 0.0
    if idx >= 1 and monthly[idx - 1].reference_price_vnd > 0:
        prev_pct = (
            (monthly[idx].reference_price_vnd - monthly[idx - 1].reference_price_vnd)
            / monthly[idx - 1].reference_price_vnd
        ) * 100.0

    def _safe(v: float | None) -> float:
        return v if v is not None and math.isfinite(v) else float("nan")

    return [
        _safe(brent_30),
        _safe(wti_30),
        _safe(fx_30),
        math.sin(angle),
        math.cos(angle),
        prev_pct,
    ]


def _raw_energy_fx_signal(feat: list[float]) -> float:
    energy_vals = [v for v in (feat[0], feat[1]) if math.isfinite(v)]
    energy = sum(energy_vals) / len(energy_vals) if energy_vals else 0.0
    fx = feat[2] if math.isfinite(feat[2]) else 0.0
    return 0.55 * energy + 0.85 * fx


def _estimate_passthrough_beta(train_samples: list[_GasMonthlySample]) -> float:
    ratios = [
        s.actual_pct / sig
        for s in train_samples
        if abs(sig := _raw_energy_fx_signal(s.features)) >= 1.0
    ]
    if not ratios:
        return 0.65
    return max(0.35, min(1.15, float(median(ratios))))


def _fit_gas_ridge(samples: list[_GasMonthlySample]) -> Pipeline:
    x_mat = np.array([s.features for s in samples], dtype=np.float64)
    y_vec = np.array([s.actual_pct for s in samples], dtype=np.float64)
    pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=15.0)),
        ]
    )
    pipe.fit(x_mat, y_vec)
    return pipe


def generate_gas_forecast(
    *,
    commodity_code: str,
    location_code: str,
    as_of_dt_vn: datetime,
    price_series: list[PriceObs],
    macro_bundle: MacroSeriesBundle,
    news_items: list[NewsObs],
) -> ForecastOutput:
    """Generate zero-look-ahead Gas monthly forecast strictly from deduplicated monthly cycles."""
    as_of_date = as_of_dt_vn.date()
    sliced_prices = slice_prices_as_of(price_series, as_of_date)
    monthly = collapse_gas_monthly_cycles(sliced_prices)
    sliced_macro = slice_macro_as_of(macro_bundle, as_of_date)
    target_date = next_gas_month_date(as_of_date)

    if len(monthly) < GAS_MIN_MONTHS:
        baseline = monthly[-1].reference_price_vnd if monthly else None
        reason = (
            f"Hiện có {len(monthly)}/{GAS_MIN_MONTHS} kỳ tháng thật (đã gộp theo tháng); "
            f"dự báo sẽ tự động kích hoạt khi tích lũy đủ {GAS_MIN_MONTHS} kỳ tháng."
        )
        return ForecastOutput(
            commodity_code=commodity_code,
            location_code=location_code,
            forecast_date=as_of_date,
            target_date=target_date,
            horizon=ForecastHorizon.NEXT_GAS_MONTH,
            model_version=GAS_PASSTHROUGH_VERSION,
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
                "raw_daily_rows": len(sliced_prices),
                "collapsed_monthly_cycles": len(monthly),
                "required_min_months": GAS_MIN_MONTHS,
                "feature_cutoff_vn": as_of_dt_vn.isoformat(),
                "timezone": VN_TZ_NAME,
            },
        )

    use_ridge = len(monthly) >= GAS_RIDGE_MIN_MONTHS
    model_version = GAS_RIDGE_VERSION if use_ridge else GAS_PASSTHROUGH_VERSION

    samples: list[_GasMonthlySample] = []
    for k in range(len(monthly) - 1):
        curr = monthly[k]
        nxt = monthly[k + 1]
        if nxt.price_date > as_of_date or curr.reference_price_vnd <= 0:
            continue
        act_pct = ((nxt.reference_price_vnd - curr.reference_price_vnd) / curr.reference_price_vnd) * 100.0
        samples.append(
            _GasMonthlySample(
                forecast_date=curr.price_date,
                target_date=nxt.price_date,
                baseline_vnd=curr.reference_price_vnd,
                actual_vnd=nxt.reference_price_vnd,
                actual_pct=act_pct,
                features=_extract_gas_features(monthly, k, sliced_macro),
            )
        )

    min_train = _RIDGE_MIN_TRAIN if use_ridge else _PASSTHROUGH_MIN_TRAIN
    bt_points: list[BacktestPoint] = []
    prior_residuals: list[float] = []

    for k in range(min_train, len(samples)):
        test_s = samples[k]
        train_slice = samples[:k]
        if use_ridge:
            pipe = _fit_gas_ridge(train_slice)
            pred_pct = float(pipe.predict(np.array([test_s.features], dtype=np.float64))[0])
        else:
            beta_k = _estimate_passthrough_beta(train_slice)
            pred_pct = beta_k * _raw_energy_fx_signal(test_s.features)
        pred_pct = max(-12.0, min(12.0, pred_pct if math.isfinite(pred_pct) else 0.0))

        if len(prior_residuals) >= 4:
            q_low, q_high = np.quantile(np.array(prior_residuals, dtype=np.float64), [0.10, 0.90])
        else:
            std_pct = float(np.std(np.array([s.actual_pct for s in train_slice], dtype=np.float64)))
            half_p = max(2.0, 1.28 * std_pct)
            q_low, q_high = -half_p, half_p

        q_low_p = min(float(q_low), -1.0)
        q_high_p = max(float(q_high), 1.0)

        pred_vnd = round_price_tick(test_s.baseline_vnd * (1.0 + pred_pct / 100.0), CommodityCategory.GAS)
        low_vnd = round_price_tick(
            test_s.baseline_vnd * (1.0 + (pred_pct + q_low_p) / 100.0),
            CommodityCategory.GAS,
        )
        high_vnd = round_price_tick(
            test_s.baseline_vnd * (1.0 + (pred_pct + q_high_p) / 100.0),
            CommodityCategory.GAS,
        )
        low_vnd = min(low_vnd, pred_vnd)
        high_vnd = max(high_vnd, pred_vnd)

        res_pct = test_s.actual_pct - pred_pct
        prior_residuals.append(res_pct)
        bt_points.append(
            build_backtest_point(
                forecast_date=test_s.forecast_date,
                target_date=test_s.target_date,
                actual_date=test_s.target_date,
                baseline_price_vnd=test_s.baseline_vnd,
                predicted_price_vnd=pred_vnd,
                lower_bound_vnd=low_vnd,
                upper_bound_vnd=high_vnd,
                actual_price_vnd=test_s.actual_vnd,
                domain=CommodityCategory.GAS,
                horizon=ForecastHorizon.NEXT_GAS_MONTH,
                commodity_code=commodity_code,
                residual_value=res_pct,
            )
        )

    summary = summarize_walk_forward(bt_points)
    latest_idx = len(monthly) - 1
    latest_obs = monthly[latest_idx]
    baseline_vnd = latest_obs.reference_price_vnd
    live_feat = _extract_gas_features(monthly, latest_idx, sliced_macro)

    if use_ridge:
        final_pipe = _fit_gas_ridge(samples)
        model_pct = float(final_pipe.predict(np.array([live_feat], dtype=np.float64))[0])
        model_pct = max(-12.0, min(12.0, model_pct if math.isfinite(model_pct) else 0.0))
        ridge_coefs = [float(c) for c in final_pipe.named_steps["ridge"].coef_]
        max_conf = 0.85
        beta_used = None
        note_vi = f"Huấn luyện hồi quy Ridge trên {len(monthly)} kỳ tháng thật."
    else:
        beta_used = _estimate_passthrough_beta(samples)
        model_pct = max(-12.0, min(12.0, beta_used * _raw_energy_fx_signal(live_feat)))
        ridge_coefs = [0.40, 0.30, 0.30, 0.0, 0.0, 0.0]
        max_conf = 0.62
        note_vi = (
            f"Mô hình Macro Pass-Through thận trọng trên {len(monthly)}/{GAS_RIDGE_MIN_MONTHS} kỳ tháng "
            "(tự động nâng cấp lên Ridge khi đủ 12 kỳ tháng)."
        )

    # First assess news risk overlay (also computes bounded CP_ARAMCO signal <= as_of_dt_vn)
    prelim_vnd = round_price_tick(baseline_vnd * (1.0 + model_pct / 100.0), CommodityCategory.GAS)
    prelim_dir = classify_direction(
        prelim_vnd - baseline_vnd,
        baseline_vnd,
        domain=CommodityCategory.GAS,
        horizon=ForecastHorizon.NEXT_GAS_MONTH,
        commodity_code=commodity_code,
    )
    q10 = min(summary.q10_error, -1.5) if summary.sample_count >= 4 else -3.0
    q90 = max(summary.q90_error, 1.5) if summary.sample_count >= 4 else 3.0

    raw_lower = round_price_tick(baseline_vnd * (1.0 + (model_pct + q10) / 100.0), CommodityCategory.GAS)
    raw_upper = round_price_tick(baseline_vnd * (1.0 + (model_pct + q90) / 100.0), CommodityCategory.GAS)
    raw_lower = min(raw_lower, prelim_vnd)
    raw_upper = max(raw_upper, prelim_vnd)

    base_conf = compute_confidence_score(
        summary,
        baseline_vnd=baseline_vnd,
        lower_vnd=raw_lower,
        upper_vnd=raw_upper,
        max_cap=max_conf,
    )
    prelim_overlay = apply_news_risk_overlay(
        domain=CommodityCategory.GAS,
        point_estimate_vnd=prelim_vnd,
        lower_bound_vnd=raw_lower,
        upper_bound_vnd=raw_upper,
        predicted_direction=prelim_dir,
        base_confidence=base_conf,
        news_items=news_items,
        cutoff_dt_vn=as_of_dt_vn,
    )

    # Integrate bounded CP_ARAMCO catalyst adjustment strictly within [-2.5%, +2.5%]
    cp_adj_pct = max(-2.5, min(2.5, prelim_overlay.cp_aramco_signal * 1.5))
    total_pct = max(-14.0, min(14.0, model_pct + cp_adj_pct))
    point_vnd = round_price_tick(baseline_vnd * (1.0 + total_pct / 100.0), CommodityCategory.GAS)
    lower_vnd = round_price_tick(baseline_vnd * (1.0 + (total_pct + q10) / 100.0), CommodityCategory.GAS)
    upper_vnd = round_price_tick(baseline_vnd * (1.0 + (total_pct + q90) / 100.0), CommodityCategory.GAS)
    lower_vnd = min(lower_vnd, point_vnd)
    upper_vnd = max(upper_vnd, point_vnd)
    if lower_vnd == point_vnd:
        lower_vnd = max(1_000, point_vnd - 1_000)
    if upper_vnd == point_vnd:
        upper_vnd = point_vnd + 1_000

    pred_dir = classify_direction(
        point_vnd - baseline_vnd,
        baseline_vnd,
        domain=CommodityCategory.GAS,
        horizon=ForecastHorizon.NEXT_GAS_MONTH,
        commodity_code=commodity_code,
    )
    final_overlay = apply_news_risk_overlay(
        domain=CommodityCategory.GAS,
        point_estimate_vnd=point_vnd,
        lower_bound_vnd=lower_vnd,
        upper_bound_vnd=upper_vnd,
        predicted_direction=pred_dir,
        base_confidence=base_conf,
        news_items=news_items,
        cutoff_dt_vn=as_of_dt_vn,
    )
    capped_conf = min(
        final_overlay.confidence_score,
        Decimal(str(max_conf)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
    )

    abs_sum = sum(abs(c) for c in ridge_coefs) or 1.0
    drivers_list = sorted(
        [
            {
                "key": key,
                "label_vi": label_vi,
                "value": None if math.isnan(val) else round(val, 2),
                "weight_pct": round(abs(coef) / abs_sum * 100.0, 1),
                "direction": "UP" if coef > 0 else ("DOWN" if coef < 0 else "FLAT"),
            }
            for (key, label_vi), val, coef in zip(_GAS_FEATURE_SPECS, live_feat, ridge_coefs, strict=True)
        ],
        key=lambda d: float(d["weight_pct"]),  # type: ignore[arg-type]
        reverse=True,
    )

    exp_delta = point_vnd - baseline_vnd
    exp_pct = (Decimal(exp_delta) / Decimal(baseline_vnd) * Decimal("100")).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )
    news_adj_dec = Decimal(str(round(cp_adj_pct, 3))).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )

    return ForecastOutput(
        commodity_code=commodity_code,
        location_code=location_code,
        forecast_date=as_of_date,
        target_date=target_date,
        horizon=ForecastHorizon.NEXT_GAS_MONTH,
        model_version=model_version,
        status=ForecastStatus.READY,
        baseline_price_vnd=baseline_vnd,
        point_estimate_vnd=point_vnd,
        lower_bound_vnd=final_overlay.lower_bound_vnd,
        upper_bound_vnd=final_overlay.upper_bound_vnd,
        expected_change_vnd=exp_delta,
        expected_change_pct=exp_pct,
        predicted_direction=pred_dir,
        confidence_score=capped_conf,
        news_adjustment_pct=news_adj_dec,
        insufficient_reason=None,
        drivers_json={
            "model_family": (
                "Ridge Regression (monthly-collapsed)"
                if use_ridge
                else f"Macro Pass-Through (beta={beta_used:.2f}, monthly-collapsed)"
            ),
            "model_note_vi": note_vi,
            "raw_daily_rows": len(sliced_prices),
            "collapsed_monthly_cycles": len(monthly),
            "target_schedule": "Kỳ điều chỉnh ngày mùng 1 hàng tháng (UTC+7)",
            "feature_cutoff_vn": as_of_dt_vn.isoformat(),
            "last_actual_date": latest_obs.price_date.isoformat(),
            "high_volatility_flag": final_overlay.high_volatility_flag,
            "band_widening_pct": final_overlay.band_widening_pct,
            "cp_aramco_news_signal": final_overlay.cp_aramco_signal,
            "feature_drivers": drivers_list,
            "news_signals": final_overlay.top_news_payload,
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

