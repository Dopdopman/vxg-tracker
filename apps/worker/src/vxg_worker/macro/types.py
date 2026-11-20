"""Shared types, units, sanity guardrails, and daily-change helpers for macro series."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from vxg_shared.enums import MacroIndicatorCode

MACRO_UNITS: dict[MacroIndicatorCode, str] = {
    MacroIndicatorCode.XAU_USD: "USD_PER_OZ",
    MacroIndicatorCode.BRENT_USD: "USD_PER_BBL",
    MacroIndicatorCode.WTI_USD: "USD_PER_BBL",
    MacroIndicatorCode.DXY: "INDEX_POINTS",
    MacroIndicatorCode.USD_VND_VCB: "VND_PER_USD",
    MacroIndicatorCode.USD_VND_MARKET: "VND_PER_USD",
}

MACRO_SANITY_BOUNDS: dict[MacroIndicatorCode, tuple[Decimal, Decimal]] = {
    MacroIndicatorCode.XAU_USD: (Decimal("1000"), Decimal("10000")),
    MacroIndicatorCode.BRENT_USD: (Decimal("20"), Decimal("250")),
    MacroIndicatorCode.WTI_USD: (Decimal("20"), Decimal("250")),
    MacroIndicatorCode.DXY: (Decimal("70"), Decimal("140")),
    MacroIndicatorCode.USD_VND_VCB: (Decimal("18000"), Decimal("35000")),
    MacroIndicatorCode.USD_VND_MARKET: (Decimal("18000"), Decimal("35000")),
}


@dataclass(frozen=True, slots=True)
class MacroPoint:
    """Validated macroeconomic or FX observation ready for DB upsert."""

    indicator_code: MacroIndicatorCode
    indicator_date: date
    value: Decimal
    open_value: Decimal | None
    high_value: Decimal | None
    low_value: Decimal | None
    change_pct_1d: Decimal | None
    unit: str
    source_code: str
    source_url: str
    content_sha256: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


def to_finite_decimal(raw_value: Any, *, places: str = "0.0001") -> Decimal | None:
    """Convert raw numeric or formatted string to a quantized finite Decimal, or None."""
    if raw_value is None or isinstance(raw_value, bool):
        return None
    text_val = str(raw_value).strip().replace(",", "")
    if not text_val:
        return None
    try:
        dec = Decimal(text_val)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite():
        return None
    return dec.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def is_within_macro_bounds(code: MacroIndicatorCode, value: Decimal) -> bool:
    """Check whether a macro value sits inside conservative operational sanity bounds."""
    if not value.is_finite() or value <= 0:
        return False
    low, high = MACRO_SANITY_BOUNDS[code]
    return low <= value <= high


def attach_change_pct_1d(
    points: list[MacroPoint],
    *,
    prior_value: Decimal | None = None,
) -> list[MacroPoint]:
    """Sort ascending, deduplicate by date, and compute 1-day percentage change."""
    if not points:
        return []

    by_date: dict[date, MacroPoint] = {}
    for pt in points:
        by_date[pt.indicator_date] = pt

    ordered = [by_date[d] for d in sorted(by_date)]
    result: list[MacroPoint] = []
    prev_val = prior_value
    for pt in ordered:
        change_pct: Decimal | None = None
        if prev_val is not None and prev_val > 0:
            change_pct = ((pt.value - prev_val) / prev_val * Decimal("100")).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        result.append(replace(pt, change_pct_1d=change_pct))
        prev_val = pt.value
    return result

