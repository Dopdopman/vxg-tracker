"""Schemas for /api/v1/prices/latest and /api/v1/prices/{record_id}/evidence."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel

from vxg_api.schemas.common import CityCode, VnDatetime
from vxg_shared.enums import (
    CommodityCategory,
    PriceUnit,
    PricingType,
    SourceTier,
    VerificationStatus,
)


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

