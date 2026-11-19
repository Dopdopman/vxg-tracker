"""Idempotent reference catalog seed for locations, commodities, and VN sources.

STRICT RULE: Never seed mock or hardcoded price_records or crawl_runs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import Commodity, Location, Source
from vxg_shared.enums import (
    CommodityCategory,
    FetchStrategy,
    FuelZoneCode,
    LocationType,
    MacroRegion,
    PriceUnit,
    PricingType,
    SourceTier,
)

SEED_LOCATIONS: list[dict[str, Any]] = [
    {
        "code": "HN",
        "name": "Hà Nội",
        "location_type": LocationType.CITY.value,
        "macro_region": MacroRegion.NORTH.value,
        "fuel_zone_code": FuelZoneCode.VUNG_1.value,
        "is_active": True,
        "display_order": 1,
    },
    {
        "code": "DN",
        "name": "Đà Nẵng",
        "location_type": LocationType.CITY.value,
        "macro_region": MacroRegion.CENTRAL.value,
        "fuel_zone_code": FuelZoneCode.VUNG_1.value,
        "is_active": True,
        "display_order": 2,
    },
    {
        "code": "HCM",
        "name": "TP. Hồ Chí Minh",
        "location_type": LocationType.CITY.value,
        "macro_region": MacroRegion.SOUTH.value,
        "fuel_zone_code": FuelZoneCode.VUNG_1.value,
        "is_active": True,
        "display_order": 3,
    },
    {
        "code": "VUNG_1",
        "name": "Vùng 1 (Cảng/Kho đầu mối)",
        "location_type": LocationType.FUEL_ZONE.value,
        "macro_region": MacroRegion.NATIONAL.value,
        "fuel_zone_code": None,
        "is_active": True,
        "display_order": 4,
    },
    {
        "code": "VUNG_2",
        "name": "Vùng 2 (Xa cảng/kho đầu mối)",
        "location_type": LocationType.FUEL_ZONE.value,
        "macro_region": MacroRegion.NATIONAL.value,
        "fuel_zone_code": None,
        "is_active": True,
        "display_order": 5,
    },
]

SEED_COMMODITIES: list[dict[str, Any]] = [
    # --- GOLD (VND/lượng, BUY_SELL) ---
    {
        "code": "GOLD_SJC_BAR_SJC",
        "category": CommodityCategory.GOLD.value,
        "brand": "SJC",
        "product_name": "Vàng miếng SJC 1L, 10L, 1KG",
        "spec_detail": "999.9",
        "unit": PriceUnit.VND_PER_LUONG.value,
        "pricing_type": PricingType.BUY_SELL.value,
        "is_active": True,
        "display_order": 1,
    },
    {
        "code": "GOLD_SJC_RING_9999",
        "category": CommodityCategory.GOLD.value,
        "brand": "SJC",
        "product_name": "Vàng nhẫn SJC 99,99%",
        "spec_detail": "1 chỉ, 2 chỉ, 5 chỉ",
        "unit": PriceUnit.VND_PER_LUONG.value,
        "pricing_type": PricingType.BUY_SELL.value,
        "is_active": True,
        "display_order": 2,
    },
    {
        "code": "GOLD_SJC_BAR_DOJI",
        "category": CommodityCategory.GOLD.value,
        "brand": "DOJI",
        "product_name": "Vàng miếng SJC (DOJI)",
        "spec_detail": "Bán lẻ DOJI",
        "unit": PriceUnit.VND_PER_LUONG.value,
        "pricing_type": PricingType.BUY_SELL.value,
        "is_active": True,
        "display_order": 3,
    },
    {
        "code": "GOLD_RING_9999_DOJI",
        "category": CommodityCategory.GOLD.value,
        "brand": "DOJI",
        "product_name": "Nhẫn tròn 9999 Hưng Thịnh Vượng",
        "spec_detail": "9999",
        "unit": PriceUnit.VND_PER_LUONG.value,
        "pricing_type": PricingType.BUY_SELL.value,
        "is_active": True,
        "display_order": 4,
    },
    {
        "code": "GOLD_SJC_BAR_PNJ",
        "category": CommodityCategory.GOLD.value,
        "brand": "PNJ",
        "product_name": "Vàng miếng SJC (PNJ)",
        "spec_detail": "Niêm yết PNJ",
        "unit": PriceUnit.VND_PER_LUONG.value,
        "pricing_type": PricingType.BUY_SELL.value,
        "is_active": True,
        "display_order": 5,
    },
    {
        "code": "GOLD_RING_9999_PNJ",
        "category": CommodityCategory.GOLD.value,
        "brand": "PNJ",
        "product_name": "Vàng nhẫn trơn PNJ 999.9",
        "spec_detail": "999.9",
        "unit": PriceUnit.VND_PER_LUONG.value,
        "pricing_type": PricingType.BUY_SELL.value,
        "is_active": True,
        "display_order": 6,
    },
    # --- FUEL (VND/lít, SINGLE_RETAIL) ---
    {
        "code": "FUEL_RON95_V",
        "category": CommodityCategory.FUEL.value,
        "brand": "PETROLIMEX",
        "product_name": "Xăng RON 95-V",
        "spec_detail": "Tiêu chuẩn khí thải mức 5",
        "unit": PriceUnit.VND_PER_LITER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 10,
    },
    {
        "code": "FUEL_RON95_III",
        "category": CommodityCategory.FUEL.value,
        "brand": "PETROLIMEX",
        "product_name": "Xăng RON 95-III",
        "spec_detail": "Phổ biến / E10 RON 95-III",
        "unit": PriceUnit.VND_PER_LITER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 11,
    },
    {
        "code": "FUEL_E5_RON92_II",
        "category": CommodityCategory.FUEL.value,
        "brand": "PETROLIMEX",
        "product_name": "Xăng E5 RON 92-II",
        "spec_detail": "Xăng sinh học E5",
        "unit": PriceUnit.VND_PER_LITER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 12,
    },
    {
        "code": "FUEL_DO_0001S_V",
        "category": CommodityCategory.FUEL.value,
        "brand": "PETROLIMEX",
        "product_name": "Dầu Diesel DO 0,001S-V",
        "spec_detail": "Tiêu chuẩn Euro 5",
        "unit": PriceUnit.VND_PER_LITER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 13,
    },
    {
        "code": "FUEL_DO_005S_II",
        "category": CommodityCategory.FUEL.value,
        "brand": "PETROLIMEX",
        "product_name": "Dầu Diesel DO 0,05S-II",
        "spec_detail": "Dầu DO phổ thông",
        "unit": PriceUnit.VND_PER_LITER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 14,
    },
    {
        "code": "FUEL_KEROSENE_2K",
        "category": CommodityCategory.FUEL.value,
        "brand": "PETROLIMEX",
        "product_name": "Dầu hỏa 2-K",
        "spec_detail": "Dầu hỏa dân dụng",
        "unit": PriceUnit.VND_PER_LITER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 15,
    },
    # --- GAS (VND/bình, SINGLE_RETAIL) ---
    {
        "code": "GAS_PETROLIMEX_12KG",
        "category": CommodityCategory.GAS.value,
        "brand": "PETROLIMEX_GAS",
        "product_name": "Gas Petrolimex Bình 12kg",
        "spec_detail": "Bình dân dụng 12kg (đã gồm VAT)",
        "unit": PriceUnit.VND_PER_CYLINDER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 20,
    },
    {
        "code": "GAS_PETROLIMEX_48KG",
        "category": CommodityCategory.GAS.value,
        "brand": "PETROLIMEX_GAS",
        "product_name": "Gas Petrolimex Bình 48kg",
        "spec_detail": "Bình công nghiệp 48kg (đã gồm VAT)",
        "unit": PriceUnit.VND_PER_CYLINDER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 21,
    },
    {
        "code": "GAS_SAIGONPETRO_12KG",
        "category": CommodityCategory.GAS.value,
        "brand": "SAIGON_PETRO",
        "product_name": "Gas Saigon Petro Bình 12kg",
        "spec_detail": "Bình dân dụng SP 12kg (đã gồm VAT)",
        "unit": PriceUnit.VND_PER_CYLINDER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 22,
    },
    {
        "code": "GAS_SAIGONPETRO_45KG",
        "category": CommodityCategory.GAS.value,
        "brand": "SAIGON_PETRO",
        "product_name": "Gas Saigon Petro Bình 45kg",
        "spec_detail": "Bình công nghiệp SP 45kg (đã gồm VAT)",
        "unit": PriceUnit.VND_PER_CYLINDER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 23,
    },
    {
        "code": "GAS_PVGAS_12KG",
        "category": CommodityCategory.GAS.value,
        "brand": "PV_GAS_LPG",
        "product_name": "Gas PV Gas LPG Bình 12kg",
        "spec_detail": "Bình dân dụng 12kg (đã gồm VAT)",
        "unit": PriceUnit.VND_PER_CYLINDER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 24,
    },
    {
        "code": "GAS_PVGAS_45KG",
        "category": CommodityCategory.GAS.value,
        "brand": "PV_GAS_LPG",
        "product_name": "Gas PV Gas LPG Bình 45kg",
        "spec_detail": "Bình công nghiệp 45kg (đã gồm VAT)",
        "unit": PriceUnit.VND_PER_CYLINDER.value,
        "pricing_type": PricingType.SINGLE_RETAIL.value,
        "is_active": True,
        "display_order": 25,
    },
]

SEED_SOURCES: list[dict[str, Any]] = [
    # --- GOLD SOURCES ---
    {
        "code": "SJC_OFFICIAL_XML",
        "name": "Công ty TNHH MTV Vàng bạc Đá quý Sài Gòn (SJC)",
        "category": CommodityCategory.GOLD.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://sjc.com.vn/gia-vang-online",
        "endpoint_url": "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx",
        "fetch_strategy": FetchStrategy.HTTP_JSON.value,
        "parser_key": "gold.sjc_json",
        "schedule_cron": "45 8 * * 1-6",
        "timeout_ms": 15000,
        "rate_limit_delay_ms": 2000,
        "is_active": True,
    },
    {
        "code": "DOJI_BOARD",
        "name": "Tập đoàn Vàng bạc Đá quý DOJI",
        "category": CommodityCategory.GOLD.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://banggia.doji.vn",
        "endpoint_url": "https://banggia.doji.vn",
        "fetch_strategy": FetchStrategy.PLAYWRIGHT_JS.value,
        "parser_key": "gold.doji_board",
        "schedule_cron": "45 8 * * 1-6",
        "timeout_ms": 35000,
        "rate_limit_delay_ms": 3000,
        "is_active": True,
    },
    {
        "code": "PNJ_HTML",
        "name": "Công ty Cổ phần Vàng bạc Đá quý Phú Nhuận (PNJ)",
        "category": CommodityCategory.GOLD.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://www.pnj.com.vn",
        "endpoint_url": "https://www.pnj.com.vn/site/gia-vang",
        "fetch_strategy": FetchStrategy.PLAYWRIGHT_JS.value,
        "parser_key": "gold.pnj_html",
        "schedule_cron": "45 8 * * 1-6",
        "timeout_ms": 30000,
        "rate_limit_delay_ms": 2500,
        "is_active": True,
    },
    {
        "code": "CAFEF_GOLD_CROSSCHECK",
        "name": "CafeF — Đối chiếu Giá vàng",
        "category": CommodityCategory.GOLD.value,
        "tier": SourceTier.SECONDARY_CROSSCHECK.value,
        "base_url": "https://cafef.vn/du-lieu/gia-vang-hom-nay/trong-nuoc.chn",
        "endpoint_url": "https://cafef.vn/du-lieu/Ajax/ajaxgoldpricehistory.ashx?index=all",
        "fetch_strategy": FetchStrategy.HTTP_JSON.value,
        "parser_key": "gold.cafef_gold",
        "schedule_cron": "45 8 * * 1-6",
        "timeout_ms": 15000,
        "rate_limit_delay_ms": 2500,
        "is_active": True,
    },
    # --- FUEL SOURCES ---
    {
        "code": "PETROLIMEX_FUEL",
        "name": "Tập đoàn Xăng dầu Việt Nam (Petrolimex)",
        "category": CommodityCategory.FUEL.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://www.petrolimex.com.vn",
        "endpoint_url": "https://www.petrolimex.com.vn",
        "fetch_strategy": FetchStrategy.PLAYWRIGHT_JS.value,
        "parser_key": "fuel.petrolimex_fuel",
        "schedule_cron": "5 7 * * *",
        "timeout_ms": 30000,
        "rate_limit_delay_ms": 2500,
        "is_active": True,
    },
    {
        "code": "PVOIL_FUEL",
        "name": "Tổng công ty Dầu Việt Nam (PVOIL) [WAF Cloudflare 403]",
        "category": CommodityCategory.FUEL.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://www.pvoil.com.vn",
        "endpoint_url": "https://www.pvoil.com.vn/truyen-thong/tin-gia-xang-dau",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "fuel.pvoil_fuel",
        "schedule_cron": "10 7 * * *",
        "timeout_ms": 20000,
        "rate_limit_delay_ms": 2500,
        "is_active": False,
    },
    {
        "code": "SAIGONPETRO_FUEL",
        "name": "Saigon Petro — Giá bán lẻ Xăng dầu",
        "category": CommodityCategory.FUEL.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://saigonpetro.com.vn",
        "endpoint_url": "https://saigonpetro.com.vn/gia-ban-le-mat-hang-xang-dau",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "fuel.saigonpetro_fuel",
        "schedule_cron": "10 7 * * *",
        "timeout_ms": 20000,
        "rate_limit_delay_ms": 2000,
        "is_active": True,
    },
    {
        "code": "VNEXPRESS_FUEL_CROSSCHECK",
        "name": "VnExpress — Giá xăng dầu [Legacy tag retired]",
        "category": CommodityCategory.FUEL.value,
        "tier": SourceTier.SECONDARY_CROSSCHECK.value,
        "base_url": "https://vnexpress.net",
        "endpoint_url": "https://vnexpress.net/chu-de/gia-xang-dau-3011",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "fuel.vnexpress_fuel",
        "schedule_cron": "15 15 * * 4",
        "timeout_ms": 15000,
        "rate_limit_delay_ms": 2500,
        "is_active": False,
    },
    {
        "code": "WEBGIA_FUEL_CROSSCHECK",
        "name": "Webgia.com — Đối chiếu giá xăng dầu Petrolimex",
        "category": CommodityCategory.FUEL.value,
        "tier": SourceTier.SECONDARY_CROSSCHECK.value,
        "base_url": "https://webgia.com",
        "endpoint_url": "https://webgia.com/gia-xang-dau/petrolimex/",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "fuel.webgia_fuel",
        "schedule_cron": "15 15 * * 4",
        "timeout_ms": 15000,
        "rate_limit_delay_ms": 2000,
        "is_active": True,
    },
    # --- GAS SOURCES ---
    {
        "code": "PETROLIMEX_GAS",
        "name": "Tổng công ty Gas Petrolimex",
        "category": CommodityCategory.GAS.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://pgas.petrolimex.com.vn",
        "endpoint_url": "https://pgas.petrolimex.com.vn",
        "fetch_strategy": FetchStrategy.PLAYWRIGHT_JS.value,
        "parser_key": "gas.petrolimex_gas",
        "schedule_cron": "30 7 * * *",
        "timeout_ms": 30000,
        "rate_limit_delay_ms": 2500,
        "is_active": True,
    },
    {
        "code": "SAIGONPETRO_GAS",
        "name": "Saigon Petro (Gas SP)",
        "category": CommodityCategory.GAS.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://saigonpetro.com.vn",
        "endpoint_url": "https://saigonpetro.com.vn/gia-ban-le",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "gas.saigonpetro_gas",
        "schedule_cron": "30 7 * * *",
        "timeout_ms": 20000,
        "rate_limit_delay_ms": 2000,
        "is_active": True,
    },
    {
        "code": "PVGAS_LPG",
        "name": "PV Gas LPG (PetroVietnam Gas)",
        "category": CommodityCategory.GAS.value,
        "tier": SourceTier.PRIMARY_OFFICIAL.value,
        "base_url": "https://pvgaslpg.com.vn",
        "endpoint_url": "https://pvgaslpg.com.vn/vi/gas-price/Ha-Noi.html",
        "fetch_strategy": FetchStrategy.PLAYWRIGHT_JS.value,
        "parser_key": "gas.pvgas_lpg",
        "schedule_cron": "30 7 * * *",
        "timeout_ms": 30000,
        "rate_limit_delay_ms": 2500,
        "is_active": True,
    },
    {
        "code": "LAODONG_GAS_CROSSCHECK",
        "name": "Báo Lao Động — Giá gas thị trường [Legacy tag retired]",
        "category": CommodityCategory.GAS.value,
        "tier": SourceTier.SECONDARY_CROSSCHECK.value,
        "base_url": "https://laodong.vn",
        "endpoint_url": "https://laodong.vn/tags/gia-gas-hom-nay-26528.ldo",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "gas.press_gas",
        "schedule_cron": "40 7 * * *",
        "timeout_ms": 15000,
        "rate_limit_delay_ms": 2500,
        "is_active": False,
    },
    {
        "code": "VIETNAMBIZ_GAS_CROSSCHECK",
        "name": "VietNamBiz — Tổng hợp & Đối chiếu giá gas",
        "category": CommodityCategory.GAS.value,
        "tier": SourceTier.SECONDARY_CROSSCHECK.value,
        "base_url": "https://vietnambiz.vn",
        "endpoint_url": "https://vietnambiz.vn/gia-gas.html",
        "fetch_strategy": FetchStrategy.HTTP_HTML.value,
        "parser_key": "gas.vietnambiz_gas",
        "schedule_cron": "40 7 * * *",
        "timeout_ms": 15000,
        "rate_limit_delay_ms": 2000,
        "is_active": True,
    },
]


async def seed_catalog(session: AsyncSession) -> dict[str, int]:
    """Upsert reference catalog tables idempotently without touching runtime prices."""
    loc_stmt = pg_insert(Location).values(SEED_LOCATIONS)
    loc_stmt = loc_stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": loc_stmt.excluded.name,
            "location_type": loc_stmt.excluded.location_type,
            "macro_region": loc_stmt.excluded.macro_region,
            "fuel_zone_code": loc_stmt.excluded.fuel_zone_code,
            "is_active": loc_stmt.excluded.is_active,
            "display_order": loc_stmt.excluded.display_order,
        },
    )
    await session.execute(loc_stmt)

    com_stmt = pg_insert(Commodity).values(SEED_COMMODITIES)
    com_stmt = com_stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "category": com_stmt.excluded.category,
            "brand": com_stmt.excluded.brand,
            "product_name": com_stmt.excluded.product_name,
            "spec_detail": com_stmt.excluded.spec_detail,
            "unit": com_stmt.excluded.unit,
            "pricing_type": com_stmt.excluded.pricing_type,
            "is_active": com_stmt.excluded.is_active,
            "display_order": com_stmt.excluded.display_order,
        },
    )
    await session.execute(com_stmt)

    src_stmt = pg_insert(Source).values(SEED_SOURCES)
    src_stmt = src_stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": src_stmt.excluded.name,
            "category": src_stmt.excluded.category,
            "tier": src_stmt.excluded.tier,
            "base_url": src_stmt.excluded.base_url,
            "endpoint_url": src_stmt.excluded.endpoint_url,
            "fetch_strategy": src_stmt.excluded.fetch_strategy,
            "parser_key": src_stmt.excluded.parser_key,
            "schedule_cron": src_stmt.excluded.schedule_cron,
            "timeout_ms": src_stmt.excluded.timeout_ms,
            "rate_limit_delay_ms": src_stmt.excluded.rate_limit_delay_ms,
            "is_active": src_stmt.excluded.is_active,
        },
    )
    await session.execute(src_stmt)

    await session.commit()
    return {
        "locations": len(SEED_LOCATIONS),
        "commodities": len(SEED_COMMODITIES),
        "sources": len(SEED_SOURCES),
    }


async def _run_seed() -> None:
    engine = create_async_db_engine()
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            counts = await seed_catalog(session)
            print(
                "Seeded catalog idempotently: "
                f"locations={counts['locations']}, "
                f"commodities={counts['commodities']}, "
                f"sources={counts['sources']}"
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run_seed())

