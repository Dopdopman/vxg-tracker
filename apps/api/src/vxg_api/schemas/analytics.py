"""Pydantic v2 schemas for macro indicators, gold world parity spread, and market news signals."""

from __future__ import annotations

import uuid
from datetime import date
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel

from vxg_api.schemas.common import VnDatetime
from vxg_api.schemas.prices import HistoryRange
from vxg_shared.enums import (
    MacroIndicatorCode,
    NewsDomain,
    NewsImpactLevel,
    NewsScoringEngine,
)


class NewsDomainFilter(StrEnum):
    ALL = "ALL"
    GOLD = "GOLD"
    FUEL = "FUEL"
    GAS = "GAS"
    MACRO = "MACRO"


class NewsWindowHours(IntEnum):
    H24 = 24
    H72 = 72
    H168 = 168


class MacroSeriesPoint(BaseModel):
    """Single historical daily observation in a macro/FX indicator series."""

    date: date
    value: float
    change_pct_1d: float | None = None
    source_code: str


class MacroLatestItem(BaseModel):
    """Latest value and short-window deltas for one macroeconomic or FX indicator."""

    indicator_code: MacroIndicatorCode
    label_vi: str
    unit: str
    date: date
    value: float
    open_value: float | None = None
    high_value: float | None = None
    low_value: float | None = None
    change_pct_1d: float | None = None
    change_pct_7d: float | None = None
    source_code: str
    source_url: str
    metadata: dict[str, Any]


class GoldWorldSpreadItem(BaseModel):
    """Transparent comparison between domestic SJC sell price and converted world XAU/USD."""

    sjc_sell_vnd: int
    sjc_date: date
    xau_usd: float
    xau_date: date
    usd_vnd: float
    fx_date: date
    fx_source: str
    conversion_factor: float
    world_vnd_per_tael: int
    spread_vnd: int
    spread_pct: float
    disclaimer_vi: str


class GoldWorldSpreadPoint(BaseModel):
    """Historical daily SJC vs converted world gold spread point."""

    date: date
    sjc_sell_vnd: int
    world_vnd_per_tael: int
    spread_vnd: int
    spread_pct: float
    fx_source: str


class MacroAnalyticsResponse(BaseModel):
    """Response payload for GET /api/v1/analytics/macro."""

    timezone: str
    as_of_vn: VnDatetime
    range: HistoryRange
    start_date: date
    end_date: date
    latest: dict[str, MacroLatestItem]
    sjc_world_spread: GoldWorldSpreadItem | None = None
    sjc_world_spread_series: list[GoldWorldSpreadPoint]
    series: dict[str, list[MacroSeriesPoint]]


class NewsSignalItem(BaseModel):
    """Structured financial/commodity news signal scored by Tier 1 lexicon + optional Gemini."""

    id: uuid.UUID
    domain: NewsDomain
    source_name: str
    title: str
    url: str
    published_at: VnDatetime
    impact_level: NewsImpactLevel
    polarity_score: float
    catalysts: list[str]
    matched_keywords: list[str]
    summary: str | None = None
    llm_summary_vi: str | None = None
    scoring_engine: NewsScoringEngine


class NewsWindowSummary(BaseModel):
    """Aggregate sentiment & top catalysts across matched news signals in the lookback window."""

    total_articles: int
    high_impact_count: int
    avg_polarity_score: float | None = None
    top_catalysts: list[str]


class NewsAnalyticsResponse(BaseModel):
    """Response payload for GET /api/v1/analytics/news."""

    timezone: str
    as_of_vn: VnDatetime
    domain_filter: NewsDomainFilter
    hours: int
    total: int
    summary: NewsWindowSummary
    items: list[NewsSignalItem]

