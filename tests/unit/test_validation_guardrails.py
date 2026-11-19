"""Unit tests for all 6 validation guardrail layers and VN currency/unit normalizers."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from vxg_shared.enums import (
    AlertSeverity,
    AlertType,
    CommodityCategory,
    PriceUnit,
    PricingType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import VN_TZ
from vxg_worker.validation.guardrails import (
    PreviousVerifiedPrice,
    PriceCandidate,
    ValidationContext,
    ValidationEngine,
)
from vxg_worker.validation.normalizer import normalize_gold_price_to_luong, parse_vnd_int

_VALID_SHA = compute_content_sha256(b"sample-evidence")


def _gold_ctx(prev: PreviousVerifiedPrice | None = None) -> ValidationContext:
    return ValidationContext(
        commodity_id=1,
        commodity_code="GOLD_SJC_BAR_SJC",
        category=CommodityCategory.GOLD,
        unit=PriceUnit.VND_PER_LUONG,
        pricing_type=PricingType.BUY_SELL,
        spec_detail="999.9",
        location_id=3,
        source_id=1,
        previous_verified=prev,
    )


def _fuel_ctx(prev: PreviousVerifiedPrice | None = None) -> ValidationContext:
    return ValidationContext(
        commodity_id=7,
        commodity_code="FUEL_RON95_III",
        category=CommodityCategory.FUEL,
        unit=PriceUnit.VND_PER_LITER,
        pricing_type=PricingType.SINGLE_RETAIL,
        spec_detail="RON 95-III",
        location_id=4,
        source_id=5,
        previous_verified=prev,
    )


def _gas_ctx(code: str = "GAS_PETROLIMEX_12KG") -> ValidationContext:
    return ValidationContext(
        commodity_id=13,
        commodity_code=code,
        category=CommodityCategory.GAS,
        unit=PriceUnit.VND_PER_CYLINDER,
        pricing_type=PricingType.SINGLE_RETAIL,
        spec_detail="Bình 12kg",
        location_id=1,
        source_id=8,
        previous_verified=None,
    )


def test_normalizer_parses_vnd_and_gold_units() -> None:
    assert parse_vnd_int("85.000.000 đ") == 85_000_000
    assert parse_vnd_int("19,840 đồng/lít") == 19_840
    assert parse_vnd_int(616_500) == 616_500
    assert parse_vnd_int(616_500.0) == 616_500
    assert parse_vnd_int(math.nan) is None
    assert parse_vnd_int(True) is None
    assert parse_vnd_int("-1000") is None

    assert normalize_gold_price_to_luong(85_000_000, source_unit="LUONG") == 85_000_000
    assert normalize_gold_price_to_luong(8_500_000, source_unit="CHI") == 85_000_000
    assert (
        normalize_gold_price_to_luong(85_000, source_unit="THOUSAND_PER_LUONG") == 85_000_000
    )
    assert normalize_gold_price_to_luong(8_500, source_unit="THOUSAND_PER_CHI") == 85_000_000


def test_layer1_rejects_bad_types_url_and_hash() -> None:
    engine = ValidationEngine()
    now_vn = datetime(2026, 11, 19, 10, 0, 0, tzinfo=VN_TZ)

    bad_url = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=85_000_000,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="ftp://invalid.local",
        evidence_sha256=_VALID_SHA,
    )
    d1 = engine.validate(bad_url, _gold_ctx())
    assert d1.verification_status == VerificationStatus.REJECTED

    bad_float = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=float("nan"),
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256=_VALID_SHA,
    )
    d2 = engine.validate(bad_float, _fuel_ctx())
    assert d2.verification_status == VerificationStatus.REJECTED

    bad_hash = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=19_840,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256="short-hash",
    )
    d3 = engine.validate(bad_hash, _fuel_ctx())
    assert d3.verification_status == VerificationStatus.REJECTED


def test_layer2_rejects_zero_negative_and_null_prices() -> None:
    engine = ValidationEngine()
    now_vn = datetime(2026, 11, 19, 10, 0, 0, tzinfo=VN_TZ)

    zero_gold = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=0,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
    )
    assert engine.validate(zero_gold, _gold_ctx()).verification_status == VerificationStatus.REJECTED

    neg_fuel = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=-19_840,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256=_VALID_SHA,
    )
    assert engine.validate(neg_fuel, _fuel_ctx()).verification_status == VerificationStatus.REJECTED


def test_layer3_business_logic_buy_sell_shape_and_spread() -> None:
    engine = ValidationEngine(gold_max_spread_pct=10.0)
    now_vn = datetime(2026, 11, 19, 10, 0, 0, tzinfo=VN_TZ)

    missing_sell = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=85_000_000,
        sell_price_vnd=None,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(missing_sell, _gold_ctx()).verification_status == VerificationStatus.REJECTED
    )

    inverted_gold = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=88_000_000,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
    )
    dec_inv = engine.validate(inverted_gold, _gold_ctx())
    assert dec_inv.verification_status == VerificationStatus.FLAGGED
    assert dec_inv.alert_severity == AlertSeverity.HIGH
    assert dec_inv.idempotency_key is not None

    excess_spread = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=70_000_000,
        sell_price_vnd=90_000_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(excess_spread, _gold_ctx()).verification_status
        == VerificationStatus.FLAGGED
    )


def test_layer4_reasonable_bounds_by_commodity() -> None:
    engine = ValidationEngine()
    now_vn = datetime(2026, 11, 19, 10, 0, 0, tzinfo=VN_TZ)

    # Gold mistakenly parsed in "chỉ" instead of "lượng" (8.5M < 30M)
    chi_mistake = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=8_500_000,
        sell_price_vnd=8_700_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(chi_mistake, _gold_ctx()).verification_status == VerificationStatus.FLAGGED
    )

    # Fuel parsed as year "2026" (< 10,000 VND/liter)
    year_mistake = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=2026,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(year_mistake, _fuel_ctx()).verification_status
        == VerificationStatus.FLAGGED
    )

    # Valid 48kg industrial gas vs out-of-range 12kg gas
    gas_48kg = PriceCandidate(
        commodity_code="GAS_PETROLIMEX_48KG",
        location_code="HN",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=2_472_768,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://pgas.petrolimex.com.vn",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(gas_48kg, _gas_ctx("GAS_PETROLIMEX_48KG")).verification_status
        == VerificationStatus.VERIFIED
    )


def test_layer5_spike_detection_and_layer6_timezone_integrity() -> None:
    engine = ValidationEngine(spike_threshold_pct=15.0)
    now_vn = datetime(2026, 11, 19, 10, 0, 0, tzinfo=VN_TZ)
    prev = PreviousVerifiedPrice(
        buy_price_vnd=85_000_000,
        sell_price_vnd=87_000_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 18),
        crawled_at=now_vn - timedelta(days=1),
    )

    # +20% spike triggers FLAGGED + PRICE_SPIKE alert
    spike = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=102_000_000,
        sell_price_vnd=104_400_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
    )
    spike_dec = engine.validate(spike, _gold_ctx(prev))
    assert spike_dec.verification_status == VerificationStatus.FLAGGED
    assert spike_dec.alert_type == AlertType.PRICE_SPIKE
    assert spike_dec.alert_severity == AlertSeverity.CRITICAL

    # Naive crawled_at -> REJECTED
    naive_dt = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=19_840,
        effective_date=date(2026, 11, 19),
        source_published_at=None,
        crawled_at=datetime(2026, 11, 19, 10, 0, 0),
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(naive_dt, _fuel_ctx()).verification_status == VerificationStatus.REJECTED
    )

    # Future source_published_at (+4 hours) -> FLAGGED
    future_pub = PriceCandidate(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        buy_price_vnd=None,
        sell_price_vnd=None,
        retail_price_vnd=19_840,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn + timedelta(hours=4),
        crawled_at=now_vn,
        source_url="https://www.petrolimex.com.vn",
        evidence_sha256=_VALID_SHA,
    )
    assert (
        engine.validate(future_pub, _fuel_ctx()).verification_status == VerificationStatus.FLAGGED
    )

    # Clean +2% move with UTC vs VN published_at hashes identically & promotes CROSS_CHECKED
    utc_pub = now_vn.astimezone(UTC)
    clean_utc = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=86_700_000,
        sell_price_vnd=88_700_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=utc_pub,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
        cross_check_source_id=4,
    )
    clean_vn = PriceCandidate(
        commodity_code="GOLD_SJC_BAR_SJC",
        location_code="HCM",
        buy_price_vnd=86_700_000,
        sell_price_vnd=88_700_000,
        retail_price_vnd=None,
        effective_date=date(2026, 11, 19),
        source_published_at=now_vn,
        crawled_at=now_vn,
        source_url="https://sjc.com.vn/xml/tygiavang.xml",
        evidence_sha256=_VALID_SHA,
        cross_check_source_id=4,
    )
    dec_utc = engine.validate(clean_utc, _gold_ctx(prev))
    dec_vn = engine.validate(clean_vn, _gold_ctx(prev))
    assert dec_utc.verification_status == VerificationStatus.CROSS_CHECKED
    assert dec_utc.idempotency_key == dec_vn.idempotency_key

