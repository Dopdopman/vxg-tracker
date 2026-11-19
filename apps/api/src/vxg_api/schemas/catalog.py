"""Catalog response schemas for GET /api/v1/catalog."""

from __future__ import annotations

from pydantic import BaseModel

from vxg_api.schemas.common import VnDatetime
from vxg_shared.enums import (
    CommodityCategory,
    FetchStrategy,
    MacroRegion,
    PriceUnit,
    PricingType,
    SourceTier,
)


class CityCatalogItem(BaseModel):
    """Tracked Phase 1 city metadata and mapped fuel pricing zone."""

    code: str
    name: str
    macro_region: MacroRegion
    fuel_zone_code: str | None = None
    display_order: int


class DomainCatalogItem(BaseModel):
    """Supported essential commodity domain."""

    code: CommodityCategory
    name: str
    default_unit: PriceUnit


class CommodityCatalogItem(BaseModel):
    """Active commodity specification."""

    code: str
    category: CommodityCategory
    brand: str
    product_name: str
    spec_detail: str | None = None
    unit: PriceUnit
    pricing_type: PricingType
    display_order: int


class SourceCatalogItem(BaseModel):
    """Active primary official or secondary cross-check data source."""

    code: str
    name: str
    category: CommodityCategory
    tier: SourceTier
    base_url: str
    endpoint_url: str
    fetch_strategy: FetchStrategy
    schedule_cron: str
    is_active: bool


class CatalogResponse(BaseModel):
    """Full catalog response for filters, labels, and active source transparency."""

    timezone: str
    server_time_vn: VnDatetime
    cities: list[CityCatalogItem]
    domains: list[DomainCatalogItem]
    commodities: list[CommodityCatalogItem]
    sources: list[SourceCatalogItem]

