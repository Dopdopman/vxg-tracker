"""Offline Golden Fixture unit tests for all 11 Vietnam commodity adapters & UTC+7 scheduler."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import Source
from vxg_shared.db.seed import SEED_COMMODITIES, SEED_LOCATIONS, SEED_SOURCES
from vxg_shared.enums import (
    AlertType,
    CommodityCategory,
    FetchStatus,
    PriceUnit,
    PricingType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import VN_TZ
from vxg_worker.adapters.base import SchemaDriftError, assert_not_soft_404
from vxg_worker.adapters.fuel import (
    PetrolimexFuelAdapter,
    SaigonPetroFuelAdapter,
    WebgiaFuelCrossCheckAdapter,
)
from vxg_worker.adapters.gas import (
    PetrolimexGasAdapter,
    PvGasLpgAdapter,
    SaigonPetroGasAdapter,
    VietnamBizGasCrossCheckAdapter,
)
from vxg_worker.adapters.gold import (
    CafefGoldCrossCheckAdapter,
    DojiBoardAdapter,
    PnjHtmlAdapter,
    SjcJsonAdapter,
)
from vxg_worker.adapters.registry import build_default_adapter_registry
from vxg_worker.fetcher.types import FetcherTier, FetchResult
from vxg_worker.scheduler import build_worker_scheduler
from vxg_worker.validation.guardrails import PriceCandidate, ValidationContext, ValidationEngine

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load_fixture(rel_path: str) -> bytes:
    return (FIXTURES_DIR / rel_path).read_bytes()


def _source_by_code(code: str) -> Source:
    row = next(s for s in SEED_SOURCES if s["code"] == code)
    return Source(
        id=1,
        code=row["code"],
        name=row["name"],
        category=row["category"],
        tier=row["tier"],
        base_url=row["base_url"],
        endpoint_url=row["endpoint_url"],
        fetch_strategy=row["fetch_strategy"],
        parser_key=row["parser_key"],
        schedule_cron=row["schedule_cron"],
        timeout_ms=row["timeout_ms"],
        rate_limit_delay_ms=row["rate_limit_delay_ms"],
        is_active=row["is_active"],
    )


def _fetch_result_from_fixture(
    rel_path: str,
    *,
    url: str,
    content_type: str = "text/html; charset=utf-8",
) -> FetchResult:
    raw = _load_fixture(rel_path)
    return FetchResult(
        request_url=url,
        final_url=url,
        http_status=200,
        content_type=content_type,
        raw_bytes=raw,
        content_sha256=compute_content_sha256(raw),
        byte_size=len(raw),
        response_time_ms=100,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.SUCCESS,
    )


def _assert_all_pass_guardrails(candidates: list[PriceCandidate]) -> None:
    comm_map = {c["code"]: c for c in SEED_COMMODITIES}
    loc_map = {loc["code"]: idx + 1 for idx, loc in enumerate(SEED_LOCATIONS)}
    engine = ValidationEngine()

    for idx, cand in enumerate(candidates, start=1):
        comm = comm_map[cand.commodity_code]
        ctx = ValidationContext(
            commodity_id=idx,
            commodity_code=comm["code"],
            category=CommodityCategory(comm["category"]),
            unit=PriceUnit(comm["unit"]),
            pricing_type=PricingType(comm["pricing_type"]),
            spec_detail=comm["spec_detail"],
            location_id=loc_map[cand.location_code],
            source_id=1,
        )
        decision = engine.validate(cand, ctx)
        assert decision.verification_status in (
            VerificationStatus.VERIFIED,
            VerificationStatus.CROSS_CHECKED,
        ), f"Guardrail failed for {cand.commodity_code}/{cand.location_code}: {decision.failure_reason}"


def test_registry_registers_all_active_seed_parser_keys() -> None:
    registry = build_default_adapter_registry()
    for src in SEED_SOURCES:
        if src["is_active"]:
            adapter = registry.resolve(src["parser_key"], smoke_only=False)
            assert adapter.parser_key == src["parser_key"]


def test_gold_adapters_golden_fixtures() -> None:
    # 1. SJC JSON
    sjc_src = _source_by_code("SJC_OFFICIAL_XML")
    sjc_res = SjcJsonAdapter().parse(
        _fetch_result_from_fixture(
            "gold/sjc_gold.json", url=sjc_src.endpoint_url, content_type="application/json"
        ),
        sjc_src,
    )
    assert sjc_res.documents_parsed == 1
    assert len(sjc_res.candidates) == 4
    sjc_map = {(c.commodity_code, c.location_code): c for c in sjc_res.candidates}
    assert sjc_map[("GOLD_SJC_BAR_SJC", "HCM")].buy_price_vnd == 133_700_000
    assert sjc_map[("GOLD_SJC_BAR_SJC", "HN")].sell_price_vnd == 136_700_000
    assert sjc_map[("GOLD_SJC_BAR_SJC", "DN")].sell_price_vnd == 136_700_000
    assert sjc_map[("GOLD_SJC_RING_9999", "HCM")].buy_price_vnd == 132_500_000
    _assert_all_pass_guardrails(sjc_res.candidates)

    # 2. DOJI Board (Nghìn VND/chỉ -> *10,000 -> VND/lượng)
    doji_src = _source_by_code("DOJI_BOARD")
    doji_res = DojiBoardAdapter().parse(
        _fetch_result_from_fixture("gold/doji_board.html", url=doji_src.endpoint_url),
        doji_src,
    )
    assert len(doji_res.candidates) == 6
    doji_map = {(c.commodity_code, c.location_code): c for c in doji_res.candidates}
    assert doji_map[("GOLD_SJC_BAR_DOJI", "HN")].buy_price_vnd == 133_700_000
    assert doji_map[("GOLD_RING_9999_DOJI", "HCM")].sell_price_vnd == 136_200_000
    _assert_all_pass_guardrails(doji_res.candidates)

    # 3. PNJ HTML (1.000đ/Chỉ -> *10,000 -> VND/lượng)
    pnj_src = _source_by_code("PNJ_HTML")
    pnj_res = PnjHtmlAdapter().parse(
        _fetch_result_from_fixture("gold/pnj_gold.html", url=pnj_src.endpoint_url),
        pnj_src,
    )
    assert len(pnj_res.candidates) == 6
    pnj_map = {(c.commodity_code, c.location_code): c for c in pnj_res.candidates}
    assert pnj_map[("GOLD_SJC_BAR_PNJ", "DN")].sell_price_vnd == 136_700_000
    assert pnj_map[("GOLD_RING_9999_PNJ", "HCM")].buy_price_vnd == 129_300_000
    _assert_all_pass_guardrails(pnj_res.candidates)

    # 4. CafeF Cross-Check JSON
    cf_src = _source_by_code("CAFEF_GOLD_CROSSCHECK")
    cf_res = CafefGoldCrossCheckAdapter().parse(
        _fetch_result_from_fixture(
            "gold/cafef_gold.json", url=cf_src.endpoint_url, content_type="application/json"
        ),
        cf_src,
    )
    assert len(cf_res.candidates) == 9
    assert cf_res.candidates[0].buy_price_vnd == 133_700_000
    assert cf_res.candidates[0].sell_price_vnd == 136_700_000


def test_fuel_adapters_golden_fixtures() -> None:
    # 1. Petrolimex Fuel (Vùng 1 & Vùng 2)
    plx_src = _source_by_code("PETROLIMEX_FUEL")
    plx_res = PetrolimexFuelAdapter().parse(
        _fetch_result_from_fixture("fuel/petrolimex_fuel.html", url=plx_src.endpoint_url),
        plx_src,
    )
    assert len(plx_res.candidates) == 12
    plx_map = {(c.commodity_code, c.location_code): c for c in plx_res.candidates}
    assert plx_map[("FUEL_RON95_III", "VUNG_1")].retail_price_vnd == 19_840
    assert plx_map[("FUEL_RON95_III", "VUNG_2")].retail_price_vnd == 20_230
    assert plx_map[("FUEL_E5_RON92_II", "VUNG_1")].retail_price_vnd == 19_390
    assert plx_map[("FUEL_DO_005S_II", "VUNG_1")].retail_price_vnd == 26_820
    assert plx_map[("FUEL_RON95_III", "VUNG_1")].effective_date == date(2026, 11, 12)
    _assert_all_pass_guardrails(plx_res.candidates)

    # 2. Saigon Petro Fuel (2-step listing -> detail)
    sp_src = _source_by_code("SAIGONPETRO_FUEL")
    sp_adapter = SaigonPetroFuelAdapter()
    detail_url = sp_adapter.extract_detail_url(
        _fetch_result_from_fixture("fuel/saigonpetro_fuel_list.html", url=sp_src.endpoint_url),
        sp_src,
    )
    assert detail_url == "https://saigonpetro.com.vn/gia-ban-le-mat-hang-xang-dau-tu-15-gio-00-phut-ngay-12112026"
    sp_res = sp_adapter.parse(
        _fetch_result_from_fixture("fuel/saigonpetro_fuel_detail.html", url=detail_url),
        sp_src,
    )
    assert len(sp_res.candidates) == 4
    sp_map = {c.commodity_code: c for c in sp_res.candidates}
    assert sp_map["FUEL_RON95_III"].retail_price_vnd == 19_840
    assert sp_map["FUEL_E5_RON92_II"].retail_price_vnd == 19_390
    assert sp_map["FUEL_DO_005S_II"].retail_price_vnd == 26_820
    assert sp_map["FUEL_DO_0001S_V"].retail_price_vnd == 28_620
    assert sp_map["FUEL_RON95_III"].source_url == detail_url
    _assert_all_pass_guardrails(sp_res.candidates)

    # 3. Webgia Fuel Cross-Check
    wg_src = _source_by_code("WEBGIA_FUEL_CROSSCHECK")
    wg_res = WebgiaFuelCrossCheckAdapter().parse(
        _fetch_result_from_fixture("fuel/webgia_fuel.html", url=wg_src.endpoint_url),
        wg_src,
    )
    assert len(wg_res.candidates) == 12
    wg_map = {(c.commodity_code, c.location_code): c for c in wg_res.candidates}
    assert wg_map[("FUEL_RON95_III", "VUNG_1")].retail_price_vnd == 19_840
    assert wg_map[("FUEL_KEROSENE_2K", "VUNG_2")].retail_price_vnd == 26_920


def test_gas_adapters_golden_fixtures() -> None:
    # 1. Petrolimex Gas (HN, DN, HCM — 12kg & 48kg)
    pg_src = _source_by_code("PETROLIMEX_GAS")
    pg_res = PetrolimexGasAdapter().parse(
        _fetch_result_from_fixture("gas/petrolimex_gas.html", url=pg_src.endpoint_url),
        pg_src,
    )
    assert len(pg_res.candidates) == 6
    pg_map = {(c.commodity_code, c.location_code): c for c in pg_res.candidates}
    assert pg_map[("GAS_PETROLIMEX_12KG", "HN")].retail_price_vnd == 598_752
    assert pg_map[("GAS_PETROLIMEX_48KG", "HN")].retail_price_vnd == 2_472_768
    assert pg_map[("GAS_PETROLIMEX_12KG", "DN")].retail_price_vnd == 609_000
    assert pg_map[("GAS_PETROLIMEX_12KG", "HCM")].retail_price_vnd == 612_000
    _assert_all_pass_guardrails(pg_res.candidates)

    # 2. Saigon Petro Gas (2-step listing -> notice detail)
    spg_src = _source_by_code("SAIGONPETRO_GAS")
    spg_adapter = SaigonPetroGasAdapter()
    spg_detail_url = spg_adapter.extract_detail_url(
        _fetch_result_from_fixture("gas/saigonpetro_gas_list.html", url=spg_src.endpoint_url),
        spg_src,
    )
    assert spg_detail_url == "https://saigonpetro.com.vn/gia-ban-le-gas-tu-ngay-16112026"
    spg_res = spg_adapter.parse(
        _fetch_result_from_fixture("gas/saigonpetro_gas_detail.html", url=spg_detail_url),
        spg_src,
    )
    assert len(spg_res.candidates) == 2
    spg_map = {c.commodity_code: c for c in spg_res.candidates}
    assert spg_map["GAS_SAIGONPETRO_12KG"].retail_price_vnd == 601_500
    assert spg_map["GAS_SAIGONPETRO_45KG"].retail_price_vnd == 2_256_500
    assert spg_map["GAS_SAIGONPETRO_12KG"].effective_date == date(2026, 11, 16)
    _assert_all_pass_guardrails(spg_res.candidates)

    # 3. PV Gas LPG (HN, DN, HCM — 12kg & 45kg)
    pvg_src = _source_by_code("PVGAS_LPG")
    pvg_res = PvGasLpgAdapter().parse(
        _fetch_result_from_fixture("gas/pvgas_lpg.html", url=pvg_src.endpoint_url),
        pvg_src,
    )
    assert len(pvg_res.candidates) == 6
    pvg_map = {(c.commodity_code, c.location_code): c for c in pvg_res.candidates}
    assert pvg_map[("GAS_PVGAS_12KG", "HN")].retail_price_vnd == 656_404
    assert pvg_map[("GAS_PVGAS_12KG", "DN")].retail_price_vnd == 769_656
    assert pvg_map[("GAS_PVGAS_45KG", "DN")].retail_price_vnd == 2_886_210
    assert pvg_map[("GAS_PVGAS_12KG", "HCM")].retail_price_vnd == 687_081
    _assert_all_pass_guardrails(pvg_res.candidates)

    # 4. VietnamBiz Gas Cross-Check (2-step listing -> daily roundup)
    vb_src = _source_by_code("VIETNAMBIZ_GAS_CROSSCHECK")
    vb_adapter = VietnamBizGasCrossCheckAdapter()
    vb_detail_url = vb_adapter.extract_detail_url(
        _fetch_result_from_fixture("gas/vietnambiz_gas_list.html", url=vb_src.endpoint_url),
        vb_src,
    )
    assert vb_detail_url is not None and "gia-gas-hom-nay-" in vb_detail_url
    vb_res = vb_adapter.parse(
        _fetch_result_from_fixture("gas/vietnambiz_gas_detail.html", url=vb_detail_url),
        vb_src,
    )
    vb_map = {(c.commodity_code, c.location_code): c for c in vb_res.candidates}
    assert vb_map[("GAS_PETROLIMEX_12KG", "HN")].retail_price_vnd == 598_752
    assert vb_map[("GAS_SAIGONPETRO_12KG", "HCM")].retail_price_vnd == 601_500
    assert vb_map[("GAS_PVGAS_45KG", "HCM")].retail_price_vnd == 2_576_554


def test_soft_404_canary_cross_check_mismatch_and_scheduler() -> None:
    # 1. Soft-404 fixture triggers SchemaDriftError
    cf_src = _source_by_code("CAFEF_GOLD_CROSSCHECK")
    soft_404_fetch = _fetch_result_from_fixture(
        "gold/cafef_soft_404.html",
        url="https://cafef.vn/du-lieu/gia-vang-hom-nay.chn",
    )
    with pytest.raises(SchemaDriftError, match="Soft-404"):
        assert_not_soft_404(soft_404_fetch, cf_src)

    # 2. Broken table raises SchemaDriftError
    plx_src = _source_by_code("PETROLIMEX_FUEL")
    broken_fetch = FetchResult(
        request_url=plx_src.endpoint_url,
        final_url=plx_src.endpoint_url,
        http_status=200,
        content_type="text/html",
        raw_bytes=b"<html><title>Petrolimex</title><body><div>Maintenance</div></body></html>",
        content_sha256=compute_content_sha256(b"x"),
        byte_size=70,
        response_time_ms=50,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.SUCCESS,
    )
    with pytest.raises(SchemaDriftError):
        PetrolimexFuelAdapter().parse(broken_fetch, plx_src)

    # 3. Cross-check mismatch > 5% flags candidate cleanly
    engine = ValidationEngine()
    now_vn = datetime(2026, 11, 19, 10, 0, 0, tzinfo=VN_TZ)
    cand = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=21_500,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256=compute_content_sha256(b"x"),
        cross_check_source_id=9,
        cross_check_delta_pct=Decimal("8.367"),
    )
    ctx = ValidationContext(
        commodity_id=8,
        commodity_code="FUEL_RON95_III",
        category=CommodityCategory.FUEL,
        unit=PriceUnit.VND_PER_LITER,
        pricing_type=PricingType.SINGLE_RETAIL,
        spec_detail="RON 95-III",
        location_id=4,
        source_id=5,
    )
    dec = engine.validate(cand, ctx)
    assert dec.verification_status == VerificationStatus.FLAGGED
    assert dec.alert_type == AlertType.CROSS_CHECK_MISMATCH

    # 4. Scheduler registers 9 crawl jobs + 1 snapshot retention job in Asia/Ho_Chi_Minh
    dummy_engine = create_async_db_engine("postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker")
    scheduler = build_worker_scheduler(create_session_factory(dummy_engine))
    jobs = scheduler.get_jobs()
    assert len(jobs) == 10
    assert str(scheduler.timezone) == "Asia/Ho_Chi_Minh"
    assert {j.id for j in jobs} >= {
        "gold_open_0845",
        "fuel_daily_0705",
        "fuel_thursday_adjust",
        "gas_daily_0730",
        "gas_monthly_first",
        "snapshot_retention_0315",
    }

