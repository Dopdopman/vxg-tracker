"""Immutable internal data contracts for Milestone 9 forecasting and walk-forward evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from vxg_shared.enums import ForecastHorizon, ForecastStatus, PredictedDirection


@dataclass(frozen=True, slots=True)
class PriceObs:
    """Normalized daily price point extracted strictly from `daily_price_series`."""

    price_date: date
    reference_price_vnd: int
    is_adjustment_day: bool
    series_origin: str
    source_code: str


@dataclass(frozen=True, slots=True)
class MacroSeriesBundle:
    """Mapping from indicator code to date -> Decimal observation map."""

    by_code: dict[str, dict[date, Decimal]]


@dataclass(frozen=True, slots=True)
class NewsObs:
    """Structured market news signal eligible for cutoff-guarded risk overlay."""

    id: str
    domain: str
    publisher: str
    title: str
    url: str
    published_at_vn: datetime
    polarity_score: Decimal
    impact_level: str
    catalysts: list[str]
    summary: str | None


@dataclass(frozen=True, slots=True)
class BacktestPoint:
    """Single expanding-window out-of-sample walk-forward evaluation observation."""

    forecast_date: date
    target_date: date
    actual_date: date
    baseline_price_vnd: int
    predicted_price_vnd: int
    lower_bound_vnd: int
    upper_bound_vnd: int
    actual_price_vnd: int
    abs_error_vnd: int
    pct_error: Decimal
    predicted_direction: PredictedDirection
    actual_direction: PredictedDirection
    direction_correct: bool
    within_interval: bool
    residual_value: float


@dataclass(frozen=True, slots=True)
class WalkForwardSummary:
    """Aggregated out-of-sample accuracy diagnostics vs Naive baseline."""

    sample_count: int
    mae_vnd: int
    mape_pct: float
    naive_mae_vnd: int
    naive_mape_pct: float
    directional_accuracy_pct: float
    interval_coverage_pct: float
    q10_error: float
    q90_error: float
    points: list[BacktestPoint]


@dataclass(frozen=True, slots=True)
class ForecastOutput:
    """Model forecast output ready for `price_forecasts` and optional `BACKTEST` persistence."""

    commodity_code: str
    location_code: str
    forecast_date: date
    target_date: date
    horizon: ForecastHorizon
    model_version: str
    status: ForecastStatus
    baseline_price_vnd: int | None
    point_estimate_vnd: int | None
    lower_bound_vnd: int | None
    upper_bound_vnd: int | None
    expected_change_vnd: int | None
    expected_change_pct: Decimal | None
    predicted_direction: PredictedDirection
    confidence_score: Decimal | None
    news_adjustment_pct: Decimal
    insufficient_reason: str | None
    drivers_json: dict[str, Any] = field(default_factory=dict)
    backtest_points: list[BacktestPoint] = field(default_factory=list)

