"""Gold forecasting engine (T+1 and T+7) using regularized Ridge with zero look-ahead."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

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
from vxg_worker.forecasting.calendar import next_gold_target_date
from vxg_worker.forecasting.features import (
    compute_sjc_spread_pct_as_of,
    macro_pct_change,
    nearest_prior_value,
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

GOLD_MODEL_VERSION = "gold_ridge_v1"
GOLD_MIN_DAYS = 60
_WALK_FORWARD_MIN_TRAIN = 40
_WALK_FORWARD_MIN_GAP_ROWS = 20

_GOLD_FEATURE_SPECS: tuple[tuple[str, str], ...] = (
    ("ret_1d", "Biến động vàng nội địa 1 ngày (%)"),
    ("ret_3d", "Biến động vàng nội địa 3 ngày (%)"),
    ("ret_7d", "Biến động vàng nội địa 7 ngày (%)"),
    ("vol_7d", "Độ biến động trượt 7 ngày (%)"),
    ("xau_ret_1d", "Vàng thế giới XAU/USD 1 ngày (%)"),
    ("xau_ret_3d", "Vàng thế giới XAU/USD 3 ngày (%)"),
    ("xau_ret_7d", "Vàng thế giới XAU/USD 7 ngày (%)"),
    ("fx_ret_5d", "Tỷ giá USD/VND 5 ngày (%)"),
    ("dxy_ret_5d", "Chỉ số DXY 5 ngày (%)"),
    ("sjc_spread_pct", "Chênh lệch SJC vs Thế giới quy đổi (%)"),
)


@dataclass(frozen=True, slots=True)
class _GoldSupervisedSample:
    forecast_date: date
    target_date: date
    baseline_vnd: int
    actual_vnd: int
    actual_log_ret: float
    features: list[float]


def _price_pct_change(prices: list[PriceObs], idx: int, lag: int) -> float:
    if idx < lag:
        return float("nan")
    prev = prices[idx - lag].reference_price_vnd
    curr = prices[idx].reference_price_vnd
    if prev <= 0 or curr <= 0:
        return float("nan")
    return ((curr - prev) / prev) * 100.0


def _rolling_price_vol_7d(prices: list[PriceObs], idx: int) -> float:
    if idx < 7:
        return float("nan")
    rets: list[float] = []
    for pos in range(idx - 6, idx + 1):
        p_prev = prices[pos - 1].reference_price_vnd
        p_curr = prices[pos].reference_price_vnd
        if p_prev > 0 and p_curr > 0:
            rets.append(((p_curr - p_prev) / p_prev) * 100.0)
    if len(rets) < 4:
        return float("nan")
    return float(np.std(np.array(rets, dtype=np.float64)))


def _build_gold_features(
    prices: list[PriceObs],
    idx: int,
    macro_bundle: MacroSeriesBundle,
) -> list[float]:
    obs = prices[idx]
    d = obs.price_date
    xau_map = macro_bundle.by_code.get(MacroIndicatorCode.XAU_USD.value, {})
    dxy_map = macro_bundle.by_code.get(MacroIndicatorCode.DXY.value, {})
    fx_primary = macro_bundle.by_code.get(MacroIndicatorCode.USD_VND_VCB.value, {})
    fx_fallback = macro_bundle.by_code.get(MacroIndicatorCode.USD_VND_MARKET.value, {})
    fx_map = fx_primary if nearest_prior_value(fx_primary, d, max_lookback_days=7) else fx_fallback

    def _val_or_nan(v: float | None) -> float:
        return v if v is not None and math.isfinite(v) else float("nan")

    spread_pct = compute_sjc_spread_pct_as_of(
        macro_bundle,
        ref_date=d,
        sjc_sell_vnd=obs.reference_price_vnd,
    )
    return [
        _price_pct_change(prices, idx, 1),
        _price_pct_change(prices, idx, 3),
        _price_pct_change(prices, idx, 7),
        _rolling_price_vol_7d(prices, idx),
        _val_or_nan(macro_pct_change(xau_map, d, lag_days=1)),
        _val_or_nan(macro_pct_change(xau_map, d, lag_days=3)),
        _val_or_nan(macro_pct_change(xau_map, d, lag_days=7)),
        _val_or_nan(macro_pct_change(fx_map, d, lag_days=5)),
        _val_or_nan(macro_pct_change(dxy_map, d, lag_days=5)),
        _val_or_nan(spread_pct),
    ]


def _find_horizon_target(
    prices: list[PriceObs],
    idx: int,
    horizon_days: int,
) -> PriceObs | None:
    target_min = prices[idx].price_date + timedelta(days=horizon_days)
    target_max = target_min + timedelta(days=3)
    for j in range(idx + 1, len(prices)):
        p_date = prices[j].price_date
        if p_date < target_min:
            continue
        if p_date > target_max:
            return None
        return prices[j]
    return None


def _fit_gold_pipeline(samples: list[_GoldSupervisedSample]) -> Pipeline:
    x_mat = np.array([s.features for s in samples], dtype=np.float64)
    y_vec = np.array([s.actual_log_ret for s in samples], dtype=np.float64)
    pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=10.0)),
        ]
    )
    pipe.fit(x_mat, y_vec)
    return pipe


def _predict_clamped_log_ret(pipe: Pipeline, feat: list[float], horizon: ForecastHorizon) -> float:
    raw = float(pipe.predict(np.array([feat], dtype=np.float64))[0])
    if not math.isfinite(raw):
        raw = 0.0
    cap = 0.12 if horizon == ForecastHorizon.T_PLUS_7 else 0.06
    return max(-cap, min(cap, raw))


def generate_gold_forecast(
    *,
    commodity_code: str,
    location_code: str,
    horizon: ForecastHorizon,
    as_of_dt_vn: datetime,
    price_series: list[PriceObs],
    macro_bundle: MacroSeriesBundle,
    news_items: list[NewsObs],
) -> ForecastOutput:
    """Generate zero-look-ahead Gold forecast (T+1 or T+7) and expanding walk-forward backtest."""
    if horizon not in (ForecastHorizon.T_PLUS_1, ForecastHorizon.T_PLUS_7):
        raise ValueError(f"Unsupported Gold horizon: {horizon}")

    as_of_date = as_of_dt_vn.date()
    sliced_prices = slice_prices_as_of(price_series, as_of_date)
    sliced_macro = slice_macro_as_of(macro_bundle, as_of_date)
    target_date = next_gold_target_date(as_of_date, horizon)
    horizon_days = 7 if horizon == ForecastHorizon.T_PLUS_7 else 1

    if len(sliced_prices) < GOLD_MIN_DAYS:
        baseline = sliced_prices[-1].reference_price_vnd if sliced_prices else None
        reason = (
            f"Hiện có {len(sliced_prices)}/{GOLD_MIN_DAYS} ngày dữ liệu thật; "
            f"dự báo sẽ tự động kích hoạt khi tích lũy đủ {GOLD_MIN_DAYS} ngày."
        )
        return ForecastOutput(
            commodity_code=commodity_code,
            location_code=location_code,
            forecast_date=as_of_date,
            target_date=target_date,
            horizon=horizon,
            model_version=GOLD_MODEL_VERSION,
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
                "sample_count": len(sliced_prices),
                "required_min_days": GOLD_MIN_DAYS,
                "feature_cutoff_vn": as_of_dt_vn.isoformat(),
                "timezone": VN_TZ_NAME,
            },
        )

    samples: list[_GoldSupervisedSample] = []
    for idx in range(7, len(sliced_prices)):
        target_obs = _find_horizon_target(sliced_prices, idx, horizon_days)
        if target_obs is None or target_obs.price_date > as_of_date:
            continue
        base_vnd = sliced_prices[idx].reference_price_vnd
        act_vnd = target_obs.reference_price_vnd
        if base_vnd <= 0 or act_vnd <= 0:
            continue
        samples.append(
            _GoldSupervisedSample(
                forecast_date=sliced_prices[idx].price_date,
                target_date=target_obs.price_date,
                baseline_vnd=base_vnd,
                actual_vnd=act_vnd,
                actual_log_ret=math.log(act_vnd / base_vnd),
                features=_build_gold_features(sliced_prices, idx, sliced_macro),
            )
        )

    if len(samples) < _WALK_FORWARD_MIN_TRAIN:
        baseline = sliced_prices[-1].reference_price_vnd
        reason = (
            f"Chỉ có {len(samples)}/{_WALK_FORWARD_MIN_TRAIN} cặp mẫu kiểm thử liên tục; "
            "cần thêm chuỗi ngày liên tiếp để huấn luyện Walk-Forward."
        )
        return ForecastOutput(
            commodity_code=commodity_code,
            location_code=location_code,
            forecast_date=as_of_date,
            target_date=target_date,
            horizon=horizon,
            model_version=GOLD_MODEL_VERSION,
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
            drivers_json={"feature_cutoff_vn": as_of_dt_vn.isoformat()},
        )

    # Expanding-window walk-forward backtest (training samples strictly satisfy target_date <= forecast_date_k)
    n_samples = len(samples)
    step = 1 if (n_samples - _WALK_FORWARD_MIN_TRAIN) <= 120 else max(1, (n_samples - _WALK_FORWARD_MIN_TRAIN) // 120)
    eval_indices = list(range(_WALK_FORWARD_MIN_TRAIN, n_samples, step))
    if eval_indices[-1] != n_samples - 1:
        eval_indices.append(n_samples - 1)

    bt_points: list[BacktestPoint] = []
    prior_residuals: list[float] = []

    for k in eval_indices:
        test_sample = samples[k]
        train_slice = [s for s in samples[:k] if s.target_date <= test_sample.forecast_date]
        if len(train_slice) < _WALK_FORWARD_MIN_GAP_ROWS:
            continue
        pipe = _fit_gold_pipeline(train_slice)
        pred_lr = _predict_clamped_log_ret(pipe, test_sample.features, horizon)

        if len(prior_residuals) >= 8:
            q_low, q_high = np.quantile(np.array(prior_residuals, dtype=np.float64), [0.10, 0.90])
        else:
            train_std = float(np.std(np.array([s.actual_log_ret for s in train_slice], dtype=np.float64)))
            half_w = max(0.006, 1.28 * train_std)
            q_low, q_high = -half_w, half_w

        q_low_f = min(float(q_low), -0.002)
        q_high_f = max(float(q_high), 0.002)

        pred_vnd = round_price_tick(test_sample.baseline_vnd * math.exp(pred_lr), CommodityCategory.GOLD)
        low_vnd = round_price_tick(
            test_sample.baseline_vnd * math.exp(pred_lr + q_low_f),
            CommodityCategory.GOLD,
        )
        high_vnd = round_price_tick(
            test_sample.baseline_vnd * math.exp(pred_lr + q_high_f),
            CommodityCategory.GOLD,
        )
        low_vnd = min(low_vnd, pred_vnd)
        high_vnd = max(high_vnd, pred_vnd)

        residual = test_sample.actual_log_ret - pred_lr
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
                domain=CommodityCategory.GOLD,
                horizon=horizon,
                commodity_code=commodity_code,
                residual_value=residual,
            )
        )

    summary = summarize_walk_forward(bt_points)

    # Fit final model ONLY on supervised pairs whose target_date <= as_of_date
    final_pipe = _fit_gold_pipeline(samples)
    latest_idx = len(sliced_prices) - 1
    latest_obs = sliced_prices[latest_idx]
    baseline_vnd = latest_obs.reference_price_vnd
    live_feat = _build_gold_features(sliced_prices, latest_idx, sliced_macro)
    live_lr = _predict_clamped_log_ret(final_pipe, live_feat, horizon)

    q10 = min(summary.q10_error, -0.003) if summary.sample_count >= 8 else -0.015
    q90 = max(summary.q90_error, 0.003) if summary.sample_count >= 8 else 0.015

    point_vnd = round_price_tick(baseline_vnd * math.exp(live_lr), CommodityCategory.GOLD)
    lower_vnd = round_price_tick(baseline_vnd * math.exp(live_lr + q10), CommodityCategory.GOLD)
    upper_vnd = round_price_tick(baseline_vnd * math.exp(live_lr + q90), CommodityCategory.GOLD)
    lower_vnd = min(lower_vnd, point_vnd)
    upper_vnd = max(upper_vnd, point_vnd)
    if lower_vnd == point_vnd:
        lower_vnd = max(100_000, point_vnd - 100_000)
    if upper_vnd == point_vnd:
        upper_vnd = point_vnd + 100_000

    pred_dir = classify_direction(
        point_vnd - baseline_vnd,
        baseline_vnd,
        domain=CommodityCategory.GOLD,
        horizon=horizon,
        commodity_code=commodity_code,
    )
    base_conf = compute_confidence_score(
        summary,
        baseline_vnd=baseline_vnd,
        lower_vnd=lower_vnd,
        upper_vnd=upper_vnd,
    )
    overlay = apply_news_risk_overlay(
        domain=CommodityCategory.GOLD,
        point_estimate_vnd=point_vnd,
        lower_bound_vnd=lower_vnd,
        upper_bound_vnd=upper_vnd,
        predicted_direction=pred_dir,
        base_confidence=base_conf,
        news_items=news_items,
        cutoff_dt_vn=as_of_dt_vn,
    )

    ridge_step: Ridge = final_pipe.named_steps["ridge"]
    coefs = [float(c) for c in ridge_step.coef_]
    abs_sum = sum(abs(c) for c in coefs) or 1.0
    drivers_list = sorted(
        [
            {
                "key": key,
                "label_vi": label_vi,
                "value": None if math.isnan(val) else round(val, 3),
                "weight_pct": round(abs(coef) / abs_sum * 100.0, 1),
                "direction": "UP" if coef > 0 else ("DOWN" if coef < 0 else "FLAT"),
            }
            for (key, label_vi), val, coef in zip(_GOLD_FEATURE_SPECS, live_feat, coefs, strict=True)
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
        horizon=horizon,
        model_version=GOLD_MODEL_VERSION,
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
            "model_family": "Ridge Regression (log-return, expanding window)",
            "training_observations": len(sliced_prices),
            "feature_cutoff_vn": as_of_dt_vn.isoformat(),
            "last_actual_date": latest_obs.price_date.isoformat(),
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
