"""Zero look-ahead 3-domain forecasting engine, walk-forward backtester, and evaluator."""

from vxg_worker.forecasting.calendar import (
    next_fuel_adjustment_date,
    next_gas_month_date,
    next_gold_target_date,
)
from vxg_worker.forecasting.evaluator import (
    EvaluationRunSummary,
    evaluate_due_forecasts,
    persist_backtest_evaluations,
)
from vxg_worker.forecasting.features import (
    collapse_fuel_adjustment_cycles,
    collapse_gas_monthly_cycles,
    slice_macro_as_of,
    slice_news_as_of,
    slice_prices_as_of,
)
from vxg_worker.forecasting.fuel_model import (
    FUEL_MIN_CYCLES,
    FUEL_MODEL_VERSION,
    generate_fuel_forecast,
)
from vxg_worker.forecasting.gas_model import (
    GAS_MIN_MONTHS,
    GAS_PASSTHROUGH_VERSION,
    GAS_RIDGE_MIN_MONTHS,
    GAS_RIDGE_VERSION,
    generate_gas_forecast,
)
from vxg_worker.forecasting.gold_model import (
    GOLD_MIN_DAYS,
    GOLD_MODEL_VERSION,
    generate_gold_forecast,
)
from vxg_worker.forecasting.metrics import (
    classify_direction,
    compute_confidence_score,
    round_price_tick,
    summarize_walk_forward,
)
from vxg_worker.forecasting.news_overlay import apply_news_risk_overlay
from vxg_worker.forecasting.pipeline import (
    DEFAULT_FORECAST_LOCK_KEY,
    ForecastPipelineSummary,
    poll_queued_forecast_run,
    run_forecast_pipeline,
)
from vxg_worker.forecasting.types import (
    BacktestPoint,
    ForecastOutput,
    MacroSeriesBundle,
    NewsObs,
    PriceObs,
    WalkForwardSummary,
)

__all__ = [
    "DEFAULT_FORECAST_LOCK_KEY",
    "FUEL_MIN_CYCLES",
    "FUEL_MODEL_VERSION",
    "ForecastOutput",
    "ForecastPipelineSummary",
    "GAS_MIN_MONTHS",
    "GAS_PASSTHROUGH_VERSION",
    "GAS_RIDGE_MIN_MONTHS",
    "GAS_RIDGE_VERSION",
    "GOLD_MIN_DAYS",
    "GOLD_MODEL_VERSION",
    "BacktestPoint",
    "EvaluationRunSummary",
    "MacroSeriesBundle",
    "NewsObs",
    "PriceObs",
    "WalkForwardSummary",
    "apply_news_risk_overlay",
    "classify_direction",
    "collapse_fuel_adjustment_cycles",
    "collapse_gas_monthly_cycles",
    "compute_confidence_score",
    "evaluate_due_forecasts",
    "generate_fuel_forecast",
    "generate_gas_forecast",
    "generate_gold_forecast",
    "next_fuel_adjustment_date",
    "next_gas_month_date",
    "next_gold_target_date",
    "persist_backtest_evaluations",
    "poll_queued_forecast_run",
    "round_price_tick",
    "run_forecast_pipeline",
    "slice_macro_as_of",
    "slice_news_as_of",
    "slice_prices_as_of",
    "summarize_walk_forward",
]

