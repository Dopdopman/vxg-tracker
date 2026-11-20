"""Query services for public latest verified prices and audit evidence drawers."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from vxg_api.errors import AppError
from vxg_api.schemas.common import CityCode
from vxg_api.schemas.prices import (
    CrossCheckSourceInfo,
    HistoryLocationCode,
    HistoryRange,
    LatestPriceItem,
    LatestPricesResponse,
    PriceEvidenceResponse,
    PriceHistoryPoint,
    PriceHistoryResponse,
    PriceHistoryStats,
)
from vxg_shared.db.models import (
    Commodity,
    CrawlRun,
    CrawlSourceLog,
    DailyPriceSeries,
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
    SeriesOrigin,
    SourceTier,
    VerificationStatus,
)
from vxg_shared.timezone import VN_TZ_NAME, vn_now, vn_today

_HISTORY_RANGE_DAYS: dict[HistoryRange, int] = {
    HistoryRange.D30: 30,
    HistoryRange.D90: 90,
    HistoryRange.Y1: 365,
    HistoryRange.Y2: 731,
}

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
    pair_series: dict[tuple[int, int], list[tuple[date, int]]] = defaultdict(list)

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
        pair_ids: set[tuple[int, int]] = set()
        for record, commodity, loc, source in raw_rows:
            grouped_rows[(commodity.category, loc.code)].append((record, commodity, source))
            pair_ids.add((int(commodity.id), int(loc.id)))

        if pair_ids:
            c_ids = {cid for cid, _ in pair_ids}
            l_ids = {lid for _, lid in pair_ids}
            ranked = (
                select(
                    DailyPriceSeries.commodity_id,
                    DailyPriceSeries.location_id,
                    DailyPriceSeries.price_date,
                    DailyPriceSeries.reference_price_vnd,
                    func.row_number()
                    .over(
                        partition_by=(DailyPriceSeries.commodity_id, DailyPriceSeries.location_id),
                        order_by=DailyPriceSeries.price_date.desc(),
                    )
                    .label("rn"),
                )
                .where(
                    DailyPriceSeries.commodity_id.in_(c_ids),
                    DailyPriceSeries.location_id.in_(l_ids),
                )
                .subquery()
            )
            hist_rows = (
                await session.execute(
                    select(
                        ranked.c.commodity_id,
                        ranked.c.location_id,
                        ranked.c.price_date,
                        ranked.c.reference_price_vnd,
                    )
                    .where(ranked.c.rn <= 3)
                    .order_by(
                        ranked.c.commodity_id.asc(),
                        ranked.c.location_id.asc(),
                        ranked.c.price_date.desc(),
                    )
                )
            ).all()
            for cid, lid, p_date, ref_vnd in hist_rows:
                pair = (int(cid), int(lid))
                if pair in pair_ids:
                    pair_series[pair].append((p_date, int(ref_vnd)))

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
                current_ref = (
                    record.sell_price_vnd
                    if record.sell_price_vnd is not None
                    else record.retail_price_vnd
                )
                prev_ref = next(
                    (
                        ref_val
                        for p_date, ref_val in pair_series.get(
                            (int(commodity.id), int(record.location_id)), ()
                        )
                        if p_date < record.effective_date
                    ),
                    None,
                )
                prev_change_vnd: int | None = None
                prev_change_pct: float | None = None
                if current_ref is not None and prev_ref is not None and prev_ref > 0:
                    prev_change_vnd = int(current_ref - prev_ref)
                    prev_change_pct = round((prev_change_vnd / prev_ref) * 100.0, 2)

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
                    prev_change_vnd=prev_change_vnd,
                    prev_change_pct=prev_change_pct,
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


async def get_price_history(
    session: AsyncSession,
    *,
    commodity_code: str,
    location: HistoryLocationCode,
    history_range: HistoryRange,
) -> PriceHistoryResponse:
    """Return historical daily series for one commodity & location mapped per domain rules."""
    normalized_code = commodity_code.strip().upper()
    commodity = (
        await session.execute(
            select(Commodity).where(
                Commodity.code == normalized_code,
                Commodity.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if commodity is None:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="COMMODITY_NOT_FOUND",
            message=f"Commodity '{normalized_code}' was not found or is inactive.",
        )

    requested_loc = (
        await session.execute(
            select(Location).where(
                Location.code == location.value,
                Location.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if requested_loc is None:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="LOCATION_NOT_FOUND",
            message=f"Location '{location.value}' was not found.",
        )

    is_fuel = commodity.category == CommodityCategory.FUEL.value
    is_zone_param = location in (HistoryLocationCode.VUNG_1, HistoryLocationCode.VUNG_2)

    if is_zone_param and not is_fuel:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_LOCATION_FOR_DOMAIN",
            message=f"Location '{location.value}' is only valid for FUEL commodities, not {commodity.category}.",
        )

    if is_fuel:
        if is_zone_param:
            target_loc = requested_loc
            applied_zone: str | None = requested_loc.code
        else:
            zone_code = requested_loc.fuel_zone_code or "VUNG_1"
            applied_zone = zone_code
            target_loc_row = (
                await session.execute(select(Location).where(Location.code == zone_code))
            ).scalar_one_or_none()
            if target_loc_row is None:
                raise AppError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="LOCATION_NOT_FOUND",
                    message=f"Mapped fuel zone '{zone_code}' was not found.",
                )
            target_loc = target_loc_row
    else:
        target_loc = requested_loc
        applied_zone = None

    end_date = vn_today()
    start_date = end_date - timedelta(days=_HISTORY_RANGE_DAYS[history_range])

    rows = (
        await session.execute(
            select(DailyPriceSeries)
            .where(
                DailyPriceSeries.commodity_id == commodity.id,
                DailyPriceSeries.location_id == target_loc.id,
                DailyPriceSeries.price_date >= start_date,
                DailyPriceSeries.price_date <= end_date,
            )
            .order_by(DailyPriceSeries.price_date.asc())
        )
    ).scalars().all()

    points = [
        PriceHistoryPoint(
            date=row.price_date,
            buy_price=row.buy_price_vnd,
            sell_price=row.sell_price_vnd,
            retail_price=row.retail_price_vnd,
            reference_price=row.reference_price_vnd,
            origin=SeriesOrigin(row.series_origin),
            is_adjustment_day=row.is_adjustment_day,
            source_code=row.source_code,
            source_url=row.source_url,
        )
        for row in rows
    ]

    if points:
        ref_vals = [pt.reference_price for pt in points]
        first_val = ref_vals[0]
        latest_val = ref_vals[-1]
        change_vnd = latest_val - first_val
        change_pct = round((change_vnd / first_val) * 100.0, 2) if first_val > 0 else None
        stats = PriceHistoryStats(
            min_vnd=min(ref_vals),
            max_vnd=max(ref_vals),
            latest_vnd=latest_val,
            change_vnd=change_vnd,
            change_pct=change_pct,
            total_points=len(points),
        )
    else:
        stats = PriceHistoryStats(total_points=0)

    return PriceHistoryResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=vn_now(),
        commodity_code=commodity.code,
        commodity_name=commodity.product_name,
        brand=commodity.brand,
        domain=CommodityCategory(commodity.category),
        pricing_type=PricingType(commodity.pricing_type),
        unit=PriceUnit(commodity.unit),
        location_code=requested_loc.code,
        location_name=requested_loc.name,
        applied_zone=applied_zone,
        range=history_range,
        start_date=start_date,
        end_date=end_date,
        stats=stats,
        points=points,
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

