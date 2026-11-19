"""Catalog query service for cities, domains, commodities, and active sources."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.schemas.catalog import (
    CatalogResponse,
    CityCatalogItem,
    CommodityCatalogItem,
    DomainCatalogItem,
    SourceCatalogItem,
)
from vxg_shared.db.models import Commodity, Location, Source
from vxg_shared.enums import (
    CommodityCategory,
    FetchStrategy,
    LocationType,
    MacroRegion,
    PriceUnit,
    PricingType,
    SourceTier,
)
from vxg_shared.timezone import VN_TZ_NAME, vn_now

_DOMAINS: tuple[DomainCatalogItem, ...] = (
    DomainCatalogItem(
        code=CommodityCategory.GOLD,
        name="Giá Vàng",
        default_unit=PriceUnit.VND_PER_LUONG,
    ),
    DomainCatalogItem(
        code=CommodityCategory.FUEL,
        name="Giá Xăng Dầu",
        default_unit=PriceUnit.VND_PER_LITER,
    ),
    DomainCatalogItem(
        code=CommodityCategory.GAS,
        name="Giá Gas",
        default_unit=PriceUnit.VND_PER_CYLINDER,
    ),
)


async def get_catalog(session: AsyncSession) -> CatalogResponse:
    """Return active cities, commodity domains, commodities, and sources."""
    city_rows = (
        await session.execute(
            select(Location)
            .where(
                Location.is_active.is_(True),
                Location.location_type == LocationType.CITY.value,
            )
            .order_by(Location.display_order.asc(), Location.id.asc())
        )
    ).scalars().all()

    commodity_rows = (
        await session.execute(
            select(Commodity)
            .where(Commodity.is_active.is_(True))
            .order_by(Commodity.category.asc(), Commodity.display_order.asc(), Commodity.id.asc())
        )
    ).scalars().all()

    source_rows = (
        await session.execute(
            select(Source)
            .where(Source.is_active.is_(True))
            .order_by(Source.category.asc(), Source.tier.asc(), Source.id.asc())
        )
    ).scalars().all()

    return CatalogResponse(
        timezone=VN_TZ_NAME,
        server_time_vn=vn_now(),
        cities=[
            CityCatalogItem(
                code=loc.code,
                name=loc.name,
                macro_region=MacroRegion(loc.macro_region),
                fuel_zone_code=loc.fuel_zone_code,
                display_order=loc.display_order,
            )
            for loc in city_rows
        ],
        domains=list(_DOMAINS),
        commodities=[
            CommodityCatalogItem(
                code=comm.code,
                category=CommodityCategory(comm.category),
                brand=comm.brand,
                product_name=comm.product_name,
                spec_detail=comm.spec_detail,
                unit=PriceUnit(comm.unit),
                pricing_type=PricingType(comm.pricing_type),
                display_order=comm.display_order,
            )
            for comm in commodity_rows
        ],
        sources=[
            SourceCatalogItem(
                code=src.code,
                name=src.name,
                category=CommodityCategory(src.category),
                tier=SourceTier(src.tier),
                base_url=src.base_url,
                endpoint_url=src.endpoint_url,
                fetch_strategy=FetchStrategy(src.fetch_strategy),
                schedule_cron=src.schedule_cron,
                is_active=src.is_active,
            )
            for src in source_rows
        ],
    )

