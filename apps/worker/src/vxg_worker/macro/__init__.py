"""Macroeconomic and FX ingestion pipelines (Yahoo Chart v8 & Vietcombank)."""

from vxg_worker.macro.sync import MacroSyncSummary, run_macro_sync, upsert_macro_indicators
from vxg_worker.macro.types import (
    MACRO_SANITY_BOUNDS,
    MACRO_UNITS,
    MacroPoint,
    attach_change_pct_1d,
    is_within_macro_bounds,
)
from vxg_worker.macro.vietcombank_client import (
    MAX_VCB_LOOKBACK_DAYS,
    VCB_JSON_API_URL,
    VCB_XML_API_URL,
    fetch_vcb_rates_window,
    parse_vcb_json_rate,
    parse_vcb_xml_rate,
)
from vxg_worker.macro.yahoo_client import (
    YAHOO_TICKER_MAP,
    fetch_yahoo_indicator,
    parse_yahoo_chart_v8,
)

__all__ = [
    "MACRO_SANITY_BOUNDS",
    "MACRO_UNITS",
    "MAX_VCB_LOOKBACK_DAYS",
    "MacroPoint",
    "MacroSyncSummary",
    "VCB_JSON_API_URL",
    "VCB_XML_API_URL",
    "YAHOO_TICKER_MAP",
    "attach_change_pct_1d",
    "fetch_vcb_rates_window",
    "fetch_yahoo_indicator",
    "is_within_macro_bounds",
    "parse_vcb_json_rate",
    "parse_vcb_xml_rate",
    "parse_yahoo_chart_v8",
    "run_macro_sync",
    "upsert_macro_indicators",
]

