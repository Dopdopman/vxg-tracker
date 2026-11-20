"""Pydantic v2 schemas for forecasts, forecast detail, forecast audit, and admin forecast triggers."""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from vxg_api.schemas.common import VnDatetime
from vxg_api.schemas.prices import HistoryRange, PriceHistoryPoint, PriceHistoryStats
from vxg_shared.enums import (
    CommodityCategory,
    CrawlRunStatus,
    CrawlScope,
    ForecastEvaluationType,
    ForecastHorizon,
    ForecastStatus,
    PredictedDirection,
    PriceUnit,
    PricingType,
    TriggerType,
)

FORECAST_DISCLAIMER_VI = (
    "Phân tích định lượng và mô hình dự báo kinh tế lượng chỉ phục vụ tham khảo thông tin thị trường, "
    "không phải khuyến nghị mua bán hay đầu tư tài chính."
)


class ForecastEvaluationTypeFilter(StrEnum):
    ALL = "ALL"
    LIVE = "LIVE"
    BACKTEST = "BACKTEST"


class ForecastNewsRef(BaseModel):
    """News reference attached to a forecast snapshot."""

    id: str
    domain: str
    publisher: str
    title: str
    url: str
    published_at_vn: str | None = None
    impact_level: str
    polarity_score: float
    catalysts: list[str]
    summary: str | None = None


class ForecastFeatureDriver(BaseModel):
    """Single interpretable feature contribution weight from the trained model."""

    key: str
    label_vi: str
    value: float | None = None
    weight_pct: float
    direction: str


class ForecastBacktestMetrics(BaseModel):
    """Out-of-sample walk-forward summary compared against Naive persistence baseline."""

    sample_count: int
    mae_vnd: int | None = None
    mape_pct: float | None = None
    naive_mae_vnd: int | None = None
    naive_mape_pct: float | None = None
    directional_accuracy_pct: float | None = None
    interval_coverage_pct: float | None = None


class ForecastItem(BaseModel):
    """Canonical forecast snapshot payload returned by /forecasts/latest and /forecasts/detail."""

    forecast_id: uuid.UUID | None = None
    domain: CommodityCategory
    commodity_code: str
    commodity_name: str
    brand: str
    unit: PriceUnit
    pricing_type: PricingType
    location_code: str
    location_name: str
    applied_zone: str | None = None
    horizon_type: ForecastHorizon
    model_version: str
    status: ForecastStatus
    insufficient_reason: str | None = None
    forecast_date: date
    target_date: date
    baseline_price: int | None = None
    point_estimate: int | None = None
    lower_bound: int | None = None
    upper_bound: int | None = None
    expected_change_vnd: int | None = None
    expected_change_pct: float | None = None
    predicted_direction: PredictedDirection
    confidence_score: float | None = None
    news_adjustment_pct: float = 0.0
    high_volatility_flag: bool = False
    band_widening_pct: float = 0.0
    feature_cutoff_vn: str | None = None
    backtest_summary: ForecastBacktestMetrics | None = None
    feature_drivers: list[ForecastFeatureDriver]
    news_signals: list[ForecastNewsRef]
    drivers_json: dict[str, Any]


class LatestForecastsResponse(BaseModel):
    """Response payload for GET /api/v1/forecasts/latest."""

    timezone: str
    as_of_vn: VnDatetime
    domain_filter: CommodityCategory | None = None
    location_filter: str
    applied_zone: str | None = None
    disclaimer: str
    count: int
    items: list[ForecastItem]


class MacroHighlightCard(BaseModel):
    """Compact leading macro metric card rendered on /forecast."""

    key: str
    label_vi: str
    value_display: str
    value: float | None = None
    unit: str
    change_pct_1d: float | None = None
    change_pct_7d: float | None = None
    note_vi: str | None = None


class ForecastDetailResponse(BaseModel):
    """Unified /forecast payload bundling forecast snapshot, history chart, macro & news."""

    timezone: str
    as_of_vn: VnDatetime
    disclaimer: str
    commodity_code: str
    location_code: str
    applied_zone: str | None = None
    available_horizons: list[ForecastHorizon]
    forecast: ForecastItem
    history_range: HistoryRange
    history_stats: PriceHistoryStats
    history_points: list[PriceHistoryPoint]
    macro_highlights: list[MacroHighlightCard]
    macro_drivers: list[ForecastFeatureDriver]
    news_signals: list[ForecastNewsRef]
    backtest_summary: ForecastBacktestMetrics | None = None


class AuditEvaluationMetrics(BaseModel):
    """Aggregated error and hit-rate metrics strictly isolated by LIVE or BACKTEST type."""

    evaluation_type: ForecastEvaluationType
    total_evaluated: int
    mae_vnd: int | None = None
    mape_pct: float | None = None
    naive_mae_vnd: int | None = None
    naive_mape_pct: float | None = None
    direction_accuracy_pct: float | None = None
    interval_coverage_pct: float | None = None


class AuditForecastEvaluationItem(BaseModel):
    """Single row comparing predicted vs realized price."""

    evaluation_id: uuid.UUID
    evaluation_type: ForecastEvaluationType
    forecast_id: uuid.UUID | None = None
    domain: CommodityCategory
    commodity_code: str
    commodity_name: str
    location_code: str
    horizon_type: ForecastHorizon
    model_version: str
    forecast_date: date
    target_date: date
    actual_date: date
    baseline_price: int
    predicted_price: int
    lower_bound: int | None = None
    upper_bound: int | None = None
    actual_price: int
    abs_error_vnd: int
    pct_error: float
    predicted_direction: PredictedDirection
    actual_direction: PredictedDirection
    direction_correct: bool
    within_interval: bool
    evaluated_at_vn: VnDatetime


class LatestForecastRunSummary(BaseModel):
    """Most recent ForecastRun operational execution status."""

    id: uuid.UUID
    run_code: str
    scope_category: CrawlScope
    trigger_type: TriggerType
    status: CrawlRunStatus
    forecasts_generated: int
    evaluations_created: int
    started_at_vn: VnDatetime
    finished_at_vn: VnDatetime | None = None
    duration_ms: int | None = None
    error_summary: str | None = None


class AuditForecastsResponse(BaseModel):
    """Response payload for GET /api/v1/audit/forecasts separating LIVE vs BACKTEST."""

    timezone: str
    as_of_vn: VnDatetime
    evaluation_type_filter: ForecastEvaluationTypeFilter
    domain_filter: CommodityCategory | None = None
    horizon_filter: ForecastHorizon | None = None
    live_summary: AuditEvaluationMetrics
    backtest_summary: AuditEvaluationMetrics
    latest_forecast_run: LatestForecastRunSummary | None = None
    total: int
    limit: int
    offset: int
    items: list[AuditForecastEvaluationItem]

