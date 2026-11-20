"""Schemas for /api/v1/prices/latest and /api/v1/prices/{record_id}/evidence."""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from vxg_api.schemas.common import CityCode, VnDatetime
from vxg_shared.enums import (
    CommodityCategory,
    PriceUnit,
    PricingType,
    SeriesOrigin,
    SourceTier,
    VerificationStatus,
)


class HistoryRange(StrEnum):
    D30 = "30d"
    D90 = "90d"
    Y1 = "1y"
    Y2 = "2y"


class HistoryLocationCode(StrEnum):
    HN = "HN"
    DN = "DN"
    HCM = "HCM"
    VUNG_1 = "VUNG_1"
    VUNG_2 = "VUNG_2"


class LatestPriceItem(BaseModel):
    """Single latest verified price entry annotated with resolved zone and brand."""

    record_id: uuid.UUID
    domain: CommodityCategory
    location_code: str
    location_name: str
    applied_zone: str | None = None
    commodity_code: str
    commodity_name: str
    spec_detail: str | None = None
    brand: str
    source_code: str
    source_name: str
    pricing_type: PricingType
    unit: PriceUnit
    buy_price: int | None = None
    sell_price: int | None = None
    retail_price: int | None = None
    effective_date: date
    verification_status: VerificationStatus
    cross_check_delta_pct: float | None = None
    source_url: str
    published_at_vn: VnDatetime | None = None
    fetched_at_vn: VnDatetime
    last_verified_at_vn: VnDatetime
    prev_change_vnd: int | None = None
    prev_change_pct: float | None = None


class LatestPricesResponse(BaseModel):
    """Latest verified daily prices filtered by city and/or commodity domain."""

    timezone: str
    as_of_vn: VnDatetime
    location_filter: CityCode | None = None
    domain_filter: CommodityCategory | None = None
    fuel_zone_mapping: dict[str, str]
    count: int
    last_updated_vn: VnDatetime | None = None
    items: list[LatestPriceItem]


class CrossCheckSourceInfo(BaseModel):
    """Secondary cross-check source metadata associated with a verified record."""

    source_code: str
    source_name: str
    source_url: str
    delta_pct: float | None = None


class PriceEvidenceResponse(BaseModel):
    """Complete audit evidence payload for a single price record."""

    record_id: uuid.UUID
    domain: CommodityCategory
    commodity_code: str
    commodity_name: str
    brand: str
    location_code: str
    location_name: str
    unit: PriceUnit
    buy_price: int | None = None
    sell_price: int | None = None
    retail_price: int | None = None
    effective_date: date
    verification_status: VerificationStatus
    source_code: str
    source_name: str
    source_tier: SourceTier
    source_url: str
    run_id: uuid.UUID
    run_code: str
    source_log_id: uuid.UUID
    fetch_strategy_used: str
    http_status: int | None = None
    content_sha256: str
    snapshot_byte_size: int | None = None
    snapshot_content_type: str | None = None
    cross_check_delta_pct: float | None = None
    cross_check_sources: list[CrossCheckSourceInfo]
    published_at_vn: VnDatetime | None = None
    fetched_at_vn: VnDatetime
    last_verified_at_vn: VnDatetime
    validation_notes: dict[str, Any]


class PriceHistoryPoint(BaseModel):
    """Single daily point returned by GET /api/v1/prices/history."""

    date: date
    buy_price: int | None = None
    sell_price: int | None = None
    retail_price: int | None = None
    reference_price: int
    origin: SeriesOrigin
    is_adjustment_day: bool
    source_code: str
    source_url: str


class PriceHistoryStats(BaseModel):
    """Window summary statistics computed across returned historical points."""

    min_vnd: int | None = None
    max_vnd: int | None = None
    latest_vnd: int | None = None
    change_vnd: int | None = None
    change_pct: float | None = None
    total_points: int


class PriceHistoryResponse(BaseModel):
    """Historical price series payload backed strictly by `daily_price_series`."""

    timezone: str
    as_of_vn: VnDatetime
    commodity_code: str
    commodity_name: str
    brand: str
    domain: CommodityCategory
    pricing_type: PricingType
    unit: PriceUnit
    location_code: str
    location_name: str
    applied_zone: str | None = None
    range: HistoryRange
    start_date: date
    end_date: date
    stats: PriceHistoryStats
    points: list[PriceHistoryPoint]

