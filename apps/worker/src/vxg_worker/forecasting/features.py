"""Zero-Look-Ahead temporal slicing, domain cycle collapsing, and macro feature helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from vxg_shared.enums import MacroIndicatorCode, SeriesOrigin
from vxg_shared.gold_parity import compute_sjc_world_gold_spread, select_fx_rate_for_date
from vxg_shared.timezone import to_vn_tz
from vxg_worker.forecasting.types import MacroSeriesBundle, NewsObs, PriceObs

_GAS_MONTHLY_PRIORITY: dict[str, int] = {
    SeriesOrigin.LIVE_ROLLUP.value: 2,
}


def slice_prices_as_of(series: list[PriceObs], as_of_date: date) -> list[PriceObs]:
    """Return sorted deduplicated price observations strictly on or before `as_of_date`."""
    by_date: dict[date, PriceObs] = {}
    for pt in series:
        if pt.price_date <= as_of_date and pt.reference_price_vnd > 0:
            existing = by_date.get(pt.price_date)
            if existing is None or (
                pt.series_origin == SeriesOrigin.LIVE_ROLLUP.value
                and existing.series_origin != SeriesOrigin.LIVE_ROLLUP.value
            ):
                by_date[pt.price_date] = pt
    return [by_date[d] for d in sorted(by_date)]


def slice_macro_as_of(bundle: MacroSeriesBundle, as_of_date: date) -> MacroSeriesBundle:
    """Return a deep-sliced MacroSeriesBundle containing ONLY dates <= `as_of_date`."""
    sliced: dict[str, dict[date, Decimal]] = {
        code: {d: val for d, val in date_map.items() if d <= as_of_date}
        for code, date_map in bundle.by_code.items()
    }
    return MacroSeriesBundle(by_code=sliced)


def slice_news_as_of(
    news: list[NewsObs],
    cutoff_dt_vn: datetime,
    *,
    lookback_hours: int,
) -> list[NewsObs]:
    """Filter news strictly inside [cutoff_dt_vn - lookback_hours, cutoff_dt_vn]."""
    cutoff_vn = to_vn_tz(cutoff_dt_vn)
    floor_vn = cutoff_vn - timedelta(hours=max(1, lookback_hours))
    return [
        item
        for item in news
        if floor_vn <= to_vn_tz(item.published_at_vn) <= cutoff_vn
    ]


def collapse_fuel_adjustment_cycles(series: list[PriceObs]) -> list[PriceObs]:
    """Collapse daily fuel rows to true adjustment-cycle transitions only."""
    ordered = sorted(series, key=lambda p: p.price_date)
    if not ordered:
        return []
    cycles: list[PriceObs] = [ordered[0]]
    for pt in ordered[1:]:
        prev = cycles[-1]
        if pt.is_adjustment_day or pt.reference_price_vnd != prev.reference_price_vnd:
            cycles.append(pt)
    return cycles


def _gas_obs_rank(pt: PriceObs) -> tuple[date, int, int]:
    origin_rank = _GAS_MONTHLY_PRIORITY.get(pt.series_origin, 0)
    official_sp_rank = 1 if "SAIGONPETRO" in pt.source_code else 0
    return (pt.price_date, origin_rank, official_sp_rank)


def collapse_gas_monthly_cycles(series: list[PriceObs]) -> list[PriceObs]:
    """Collapse multi-day VietNamBiz/SaigonPetro gas rows into 1 observation per month."""
    by_ym: dict[tuple[int, int], PriceObs] = {}
    for pt in sorted(series, key=lambda p: p.price_date):
        ym = (pt.price_date.year, pt.price_date.month)
        existing = by_ym.get(ym)
        if existing is None or _gas_obs_rank(pt) > _gas_obs_rank(existing):
            by_ym[ym] = pt
    return [by_ym[ym] for ym in sorted(by_ym)]


def nearest_prior_value(
    date_map: dict[date, Decimal],
    ref_date: date,
    *,
    max_lookback_days: int = 7,
) -> tuple[date, Decimal] | None:
    """Find latest macro value strictly <= `ref_date` within `max_lookback_days`."""
    if not date_map:
        return None
    for offset in range(max(0, max_lookback_days) + 1):
        probe = ref_date - timedelta(days=offset)
        if probe in date_map:
            return probe, date_map[probe]
    return None


def rolling_window_mean(
    date_map: dict[date, Decimal],
    end_date: date,
    *,
    window_days: int,
) -> float | None:
    """Compute arithmetic mean over dates strictly inside (end_date - window_days, end_date]."""
    start_exclusive = end_date - timedelta(days=max(1, window_days))
    vals = [
        float(val)
        for d, val in date_map.items()
        if start_exclusive < d <= end_date and val > 0
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)


def macro_pct_change(
    date_map: dict[date, Decimal],
    ref_date: date,
    *,
    lag_days: int,
    max_lookback_days: int = 7,
) -> float | None:
    """Compute % change from nearest point <= ref_date - lag_days to nearest point <= ref_date."""
    curr = nearest_prior_value(date_map, ref_date, max_lookback_days=max_lookback_days)
    prev = nearest_prior_value(
        date_map,
        ref_date - timedelta(days=lag_days),
        max_lookback_days=max_lookback_days + 3,
    )
    if curr is None or prev is None or prev[1] <= 0:
        return None
    return float((curr[1] - prev[1]) / prev[1] * Decimal("100"))


def rolling_mean_pct_change(
    date_map: dict[date, Decimal],
    ref_date: date,
    *,
    window_days: int,
) -> float | None:
    """Compute % change between recent rolling mean and preceding equal-length window."""
    curr_mean = rolling_window_mean(date_map, ref_date, window_days=window_days)
    prev_mean = rolling_window_mean(
        date_map,
        ref_date - timedelta(days=window_days),
        window_days=window_days,
    )
    if curr_mean is None or prev_mean is None or prev_mean <= 0:
        return None
    return ((curr_mean - prev_mean) / prev_mean) * 100.0


def select_usd_vnd_as_of(
    bundle: MacroSeriesBundle,
    ref_date: date,
    *,
    max_lookback_days: int = 7,
) -> tuple[Decimal, date, str] | None:
    """Select USD/VND <= ref_date prioritizing Vietcombank official sell rate."""
    return select_fx_rate_for_date(
        ref_date,
        vcb_by_date=bundle.by_code.get(MacroIndicatorCode.USD_VND_VCB.value, {}),
        market_by_date=bundle.by_code.get(MacroIndicatorCode.USD_VND_MARKET.value, {}),
        max_lookback_days=max_lookback_days,
    )


def compute_sjc_spread_pct_as_of(
    bundle: MacroSeriesBundle,
    *,
    ref_date: date,
    sjc_sell_vnd: int,
) -> float | None:
    """Compute SJC domestic vs converted world gold % premium strictly using data <= ref_date."""
    xau_hit = nearest_prior_value(
        bundle.by_code.get(MacroIndicatorCode.XAU_USD.value, {}),
        ref_date,
        max_lookback_days=7,
    )
    fx_hit = select_usd_vnd_as_of(bundle, ref_date, max_lookback_days=7)
    if xau_hit is None or fx_hit is None or sjc_sell_vnd <= 0:
        return None
    xau_date, xau_val = xau_hit
    fx_val, fx_date, fx_source = fx_hit
    spread = compute_sjc_world_gold_spread(
        xau_usd=xau_val,
        usd_vnd=fx_val,
        sjc_sell_vnd=sjc_sell_vnd,
        fx_source=fx_source,
        xau_date=xau_date,
        fx_date=fx_date,
        sjc_date=ref_date,
    )
    return spread.spread_pct

