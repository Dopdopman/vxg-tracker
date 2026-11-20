"""Pure-stdlib Vietnamese tael <-> troy ounce gold world parity & spread calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

TAEL_GRAMS = Decimal("37.5")
TROY_OZ_GRAMS = Decimal("31.1034768")
TAEL_PER_TROY_OZ = TAEL_GRAMS / TROY_OZ_GRAMS

GOLD_PARITY_DISCLAIMER_VI = (
    "Quy đổi 1 lượng = 37,5g / 31,1034768g (1 troy oz), "
    "chưa bao gồm thuế nhập khẩu, chi phí vận chuyển và phí gia công."
)


@dataclass(frozen=True, slots=True)
class GoldWorldSpread:
    """Computed SJC domestic sell price vs converted world gold parity in VND per tael."""

    sjc_sell_vnd: int
    sjc_date: date
    xau_usd: float
    xau_date: date
    usd_vnd: float
    fx_date: date
    fx_source: str
    conversion_factor: float
    world_vnd_per_tael: int
    spread_vnd: int
    spread_pct: float
    disclaimer_vi: str


def compute_sjc_world_gold_spread(
    *,
    xau_usd: Decimal,
    usd_vnd: Decimal,
    sjc_sell_vnd: int,
    fx_source: str,
    xau_date: date,
    fx_date: date,
    sjc_date: date,
) -> GoldWorldSpread:
    """Compute domestic SJC vs converted world gold price and percentage spread."""
    if xau_usd <= 0:
        raise ValueError(f"xau_usd must be positive, got {xau_usd}")
    if usd_vnd <= 0:
        raise ValueError(f"usd_vnd must be positive, got {usd_vnd}")
    if sjc_sell_vnd <= 0:
        raise ValueError(f"sjc_sell_vnd must be positive, got {sjc_sell_vnd}")

    world_exact = xau_usd * (TAEL_GRAMS / TROY_OZ_GRAMS) * usd_vnd
    world_vnd_per_tael = int(world_exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    spread_vnd = sjc_sell_vnd - world_vnd_per_tael
    spread_pct = float(
        (Decimal(spread_vnd) / Decimal(world_vnd_per_tael) * Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )
    return GoldWorldSpread(
        sjc_sell_vnd=sjc_sell_vnd,
        sjc_date=sjc_date,
        xau_usd=float(xau_usd),
        xau_date=xau_date,
        usd_vnd=float(usd_vnd),
        fx_date=fx_date,
        fx_source=fx_source,
        conversion_factor=round(float(TAEL_PER_TROY_OZ), 6),
        world_vnd_per_tael=world_vnd_per_tael,
        spread_vnd=spread_vnd,
        spread_pct=spread_pct,
        disclaimer_vi=GOLD_PARITY_DISCLAIMER_VI,
    )


def select_fx_rate_for_date(
    target_date: date,
    *,
    vcb_by_date: dict[date, Decimal],
    market_by_date: dict[date, Decimal],
    max_lookback_days: int = 5,
) -> tuple[Decimal, date, str] | None:
    """Select USD/VND preferring official Vietcombank sell rate over market FX fallback."""
    if target_date in vcb_by_date:
        return vcb_by_date[target_date], target_date, "USD_VND_VCB"
    if target_date in market_by_date:
        return market_by_date[target_date], target_date, "USD_VND_MARKET"

    for offset in range(1, max(0, max_lookback_days) + 1):
        probe_date = target_date - timedelta(days=offset)
        if probe_date in vcb_by_date:
            return vcb_by_date[probe_date], probe_date, "USD_VND_VCB"
        if probe_date in market_by_date:
            return market_by_date[probe_date], probe_date, "USD_VND_MARKET"
    return None

