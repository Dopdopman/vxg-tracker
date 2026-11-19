"""Query services for public latest verified prices and audit evidence drawers."""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from vxg_api.errors import AppError
from vxg_api.schemas.common import CityCode
from vxg_api.schemas.prices import (
    CrossCheckSourceInfo,
    LatestPriceItem,
    LatestPricesResponse,
    PriceEvidenceResponse,
)
from vxg_shared.db.models import (
    Commodity,
    CrawlRun,
    CrawlSourceLog,
    Location,
    PriceRecord,
    RawEvidenceSnapshot,
    Source,
)
from vxg_shared.enums import (
    CommodityCategory,
    LocationType,
    PriceUnit,
    PricingType,
    SourceTier,
    VerificationStatus,
)
from vxg_shared.timezone import VN_TZ_NAME, vn_now

# Public price table strictly exposes VERIFIED / CROSS_CHECKED records only.
PUBLIC_VERIFIED_STATUSES: tuple[str, ...] = (
    VerificationStatus.VERIFIED.value,
    VerificationStatus.CROSS_CHECKED.value,
)

_DOMAIN_ORDER: dict[str, int] = {
    CommodityCategory.GOLD.value: 0,
    CommodityCategory.FUEL.value: 1,
    CommodityCategory.GAS.value: 2,
}

# Fuel commodities share PETROLIMEX brand seed metadata, while multiple official
# fuel retailers (e.g. Saigon Petro) report under distinct Source rows.
_FUEL_SOURCE_BRAND_OVERRIDES: dict[str, str] = {
    "PETROLIMEX_FUEL": "PETROLIMEX",
    "SAIGONPETRO_FUEL": "SAIGON_PETRO",
    "PVOIL_FUEL": "PVOIL",
}


def resolve_item_brand(commodity: Commodity, source: Source) -> str:
    """Resolve the supplier brand label so co-reported fuel brands remain distinct."""
    if commodity.category == CommodityCategory.FUEL.value:
        override = _FUEL_SOURCE_BRAND_OVERRIDES.get(source.code)
        if override is not None:
            return override
    return commodity.brand


async def get_latest_prices(
    session: AsyncSession,
    *,
    location: CityCode | None = None,
    domain: CommodityCategory | None = None,
) -> LatestPricesResponse:
    """Return latest clean VERIFIED/CROSS_CHECKED prices mapped per requested city & domain."""
    city_stmt = (
        select(Location)
        .where(
            Location.is_active.is_(True),
            Location.location_type == LocationType.CITY.value,
        )
        .order_by(Location.display_order.asc(), Location.id.asc())
    )
    if location is not None:
        city_stmt = city_stmt.where(Location.code == location.value)

    cities = list((await session.execute(city_stmt)).scalars().all())
    fuel_zone_mapping: dict[str, str] = {
        city.code: city.fuel_zone_code
        for city in cities
        if city.fuel_zone_code is not None
    }

    needed_location_codes: set[str] = {city.code for city in cities}
    if domain in (None, CommodityCategory.FUEL):
        needed_location_codes.update(fuel_zone_mapping.values())

    grouped_rows: dict[tuple[str, str], list[tuple[PriceRecord, Commodity, Source]]] = defaultdict(
        list
    )
    if needed_location_codes:
        stmt = (
            select(PriceRecord, Commodity, Location, Source)
            .join(Commodity, PriceRecord.commodity_id == Commodity.id)
            .join(Location, PriceRecord.location_id == Location.id)
            .join(Source, PriceRecord.source_id == Source.id)
            .where(
                PriceRecord.verification_status.in_(PUBLIC_VERIFIED_STATUSES),
                Commodity.is_active.is_(True),
                Location.code.in_(needed_location_codes),
            )
            .distinct(PriceRecord.commodity_id, PriceRecord.location_id, PriceRecord.source_id)
            .order_by(
                PriceRecord.commodity_id.asc(),
                PriceRecord.location_id.asc(),
                PriceRecord.source_id.asc(),
                PriceRecord.effective_date.desc(),
                func.coalesce(PriceRecord.source_published_at, PriceRecord.crawled_at).desc(),
                PriceRecord.crawled_at.desc(),
            )
        )
        if domain is not None:
            stmt = stmt.where(Commodity.category == domain.value)

        raw_rows = (await session.execute(stmt)).all()
        for record, commodity, loc, source in raw_rows:
            grouped_rows[(commodity.category, loc.code)].append((record, commodity, source))

    sortable_items: list[tuple[tuple[int, int, int, str], LatestPriceItem]] = []
    target_categories: tuple[CommodityCategory, ...] = (
        (domain,)
        if domain is not None
        else (CommodityCategory.GOLD, CommodityCategory.FUEL, CommodityCategory.GAS)
    )

    for city_order, city in enumerate(cities):
        for category in target_categories:
            if category == CommodityCategory.FUEL:
                lookup_code = city.fuel_zone_code
                applied_zone = city.fuel_zone_code
            else:
                lookup_code = city.code
                applied_zone = None

            if lookup_code is None:
                continue

            for record, commodity, source in grouped_rows.get((category.value, lookup_code), ()):
                item = LatestPriceItem(
                    record_id=record.id,
                    domain=CommodityCategory(commodity.category),
                    location_code=city.code,
                    location_name=city.name,
                    applied_zone=applied_zone,
                    commodity_code=commodity.code,
                    commodity_name=commodity.product_name,
                    spec_detail=commodity.spec_detail,
                    brand=resolve_item_brand(commodity, source),
                    source_code=source.code,
                    source_name=source.name,
                    pricing_type=PricingType(commodity.pricing_type),
                    unit=PriceUnit(commodity.unit),
                    buy_price=record.buy_price_vnd,
                    sell_price=record.sell_price_vnd,
                    retail_price=record.retail_price_vnd,
                    effective_date=record.effective_date,
                    verification_status=VerificationStatus(record.verification_status),
                    cross_check_delta_pct=(
                        float(record.cross_check_delta_pct)
                        if record.cross_check_delta_pct is not None
                        else None
                    ),
                    source_url=record.source_url,
                    published_at_vn=record.source_published_at,
                    fetched_at_vn=record.crawled_at,
                    last_verified_at_vn=record.last_verified_at,
                )
                sort_key = (
                    _DOMAIN_ORDER.get(commodity.category, 99),
                    city_order,
                    commodity.display_order,
                    source.code,
                )
                sortable_items.append((sort_key, item))

    sortable_items.sort(key=lambda pair: pair[0])
    items = [item for _, item in sortable_items]
    last_updated_vn = max((item.last_verified_at_vn for item in items), default=None)

    return LatestPricesResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=vn_now(),
        location_filter=location,
        domain_filter=domain,
        fuel_zone_mapping=fuel_zone_mapping,
        count=len(items),
        last_updated_vn=last_updated_vn,
        items=items,
    )


async def get_price_evidence(
    session: AsyncSession,
    record_id: uuid.UUID,
) -> PriceEvidenceResponse:
    """Return full provenance, run telemetry, and cross-check evidence for one PriceRecord."""
    cross_source_alias = aliased(Source)

    stmt = (
        select(
            PriceRecord,
            Commodity,
            Location,
            Source,
            CrawlRun,
            CrawlSourceLog,
            cross_source_alias,
            RawEvidenceSnapshot,
        )
        .join(Commodity, PriceRecord.commodity_id == Commodity.id)
        .join(Location, PriceRecord.location_id == Location.id)
        .join(Source, PriceRecord.source_id == Source.id)
        .join(CrawlRun, PriceRecord.crawl_run_id == CrawlRun.id)
        .join(CrawlSourceLog, PriceRecord.source_log_id == CrawlSourceLog.id)
        .outerjoin(cross_source_alias, PriceRecord.cross_check_source_id == cross_source_alias.id)
        .outerjoin(
            RawEvidenceSnapshot,
            CrawlSourceLog.snapshot_id == RawEvidenceSnapshot.id,
        )
        .where(PriceRecord.id == record_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PRICE_RECORD_NOT_FOUND",
            message=f"Price record '{record_id}' was not found.",
        )

    record, commodity, location, primary_source, crawl_run, source_log, cross_source, snapshot = row
    delta_pct = (
        float(record.cross_check_delta_pct)
        if record.cross_check_delta_pct is not None
        else None
    )
    cross_check_sources: list[CrossCheckSourceInfo] = []
    if cross_source is not None:
        cross_check_sources.append(
            CrossCheckSourceInfo(
                source_code=cross_source.code,
                source_name=cross_source.name,
                source_url=cross_source.endpoint_url,
                delta_pct=delta_pct,
            )
        )

    return PriceEvidenceResponse(
        record_id=record.id,
        domain=CommodityCategory(commodity.category),
        commodity_code=commodity.code,
        commodity_name=commodity.product_name,
        brand=resolve_item_brand(commodity, primary_source),
        location_code=location.code,
        location_name=location.name,
        unit=PriceUnit(commodity.unit),
        buy_price=record.buy_price_vnd,
        sell_price=record.sell_price_vnd,
        retail_price=record.retail_price_vnd,
        effective_date=record.effective_date,
        verification_status=VerificationStatus(record.verification_status),
        source_code=primary_source.code,
        source_name=primary_source.name,
        source_tier=SourceTier(primary_source.tier),
        source_url=record.source_url,
        run_id=crawl_run.id,
        run_code=crawl_run.run_code,
        source_log_id=source_log.id,
        fetch_strategy_used=source_log.fetch_strategy_used,
        http_status=source_log.http_status,
        content_sha256=record.evidence_sha256,
        snapshot_byte_size=snapshot.byte_size if snapshot is not None else None,
        snapshot_content_type=snapshot.content_type if snapshot is not None else None,
        cross_check_delta_pct=delta_pct,
        cross_check_sources=cross_check_sources,
        published_at_vn=record.source_published_at,
        fetched_at_vn=record.crawled_at,
        last_verified_at_vn=record.last_verified_at,
        validation_notes=dict(record.validation_notes),
    )

