"""Read-only database queries for macro indicators, gold parity spread, and market news signals."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.schemas.analytics import (
    GoldWorldSpreadItem,
    GoldWorldSpreadPoint,
    MacroAnalyticsResponse,
    MacroLatestItem,
    MacroSeriesPoint,
    NewsAnalyticsResponse,
    NewsDomainFilter,
    NewsSignalItem,
    NewsWindowSummary,
)
from vxg_api.schemas.prices import HistoryRange
from vxg_shared.db.models import (
    Commodity,
    DailyPriceSeries,
    Location,
    MacroIndicator,
    MarketNewsSignal,
)
from vxg_shared.enums import (
    MacroIndicatorCode,
    NewsDomain,
    NewsImpactLevel,
    NewsScoringEngine,
)
from vxg_shared.gold_parity import compute_sjc_world_gold_spread, select_fx_rate_for_date
from vxg_shared.timezone import VN_TZ_NAME, vn_now, vn_today

_HISTORY_RANGE_DAYS: dict[HistoryRange, int] = {
    HistoryRange.D30: 30,
    HistoryRange.D90: 90,
    HistoryRange.Y1: 365,
    HistoryRange.Y2: 730,
}

_INDICATOR_LABELS_VI: dict[MacroIndicatorCode, str] = {
    MacroIndicatorCode.XAU_USD: "Vàng thế giới (XAU/USD)",
    MacroIndicatorCode.BRENT_USD: "Dầu thô Brent",
    MacroIndicatorCode.WTI_USD: "Dầu thô WTI",
    MacroIndicatorCode.DXY: "Chỉ số USD Index (DXY)",
    MacroIndicatorCode.USD_VND_VCB: "Tỷ giá USD/VND (Vietcombank bán ra)",
    MacroIndicatorCode.USD_VND_MARKET: "Tỷ giá USD/VND thị trường (VND=X)",
}


def _find_nearest_prior(
    target_date: date,
    series_map: dict[date, Decimal],
    *,
    max_lookback_days: int = 4,
) -> tuple[Decimal, date] | None:
    if target_date in series_map:
        return series_map[target_date], target_date
    for offset in range(1, max(0, max_lookback_days) + 1):
        probe = target_date - timedelta(days=offset)
        if probe in series_map:
            return series_map[probe], probe
    return None


def _compute_7d_change_pct(rows: list[MacroIndicator]) -> float | None:
    if len(rows) < 2:
        return None
    latest = rows[-1]
    target_prior = latest.indicator_date - timedelta(days=7)
    prior_candidates = [r for r in rows if r.indicator_date <= target_prior]
    if not prior_candidates:
        return None
    prior_row = prior_candidates[-1]
    if prior_row.value <= 0:
        return None
    pct = (latest.value - prior_row.value) / prior_row.value * Decimal("100")
    return round(float(pct), 2)


async def get_macro_analytics(
    session: AsyncSession,
    *,
    indicators: list[MacroIndicatorCode] | None,
    history_range: HistoryRange,
) -> MacroAnalyticsResponse:
    """Return latest macro snapshots, historical series, and SJC-vs-world gold spread."""
    requested_codes = indicators or list(MacroIndicatorCode)
    end_date = vn_today()
    start_date = end_date - timedelta(days=_HISTORY_RANGE_DAYS[history_range])
    fetch_start = start_date - timedelta(days=14)

    parity_codes = {
        MacroIndicatorCode.XAU_USD,
        MacroIndicatorCode.USD_VND_VCB,
        MacroIndicatorCode.USD_VND_MARKET,
    }
    needed_codes = [c.value for c in (set(requested_codes) | parity_codes)]

    rows = (
        await session.execute(
            select(MacroIndicator)
            .where(
                MacroIndicator.indicator_code.in_(needed_codes),
                MacroIndicator.indicator_date >= fetch_start,
                MacroIndicator.indicator_date <= end_date,
            )
            .order_by(MacroIndicator.indicator_code.asc(), MacroIndicator.indicator_date.asc())
        )
    ).scalars().all()

    by_code: dict[str, list[MacroIndicator]] = defaultdict(list)
    for row in rows:
        by_code[row.indicator_code].append(row)

    latest_payload: dict[str, MacroLatestItem] = {}
    series_payload: dict[str, list[MacroSeriesPoint]] = {}

    for code in requested_codes:
        code_rows = by_code.get(code.value, [])
        window_rows = [r for r in code_rows if r.indicator_date >= start_date]
        series_payload[code.value] = [
            MacroSeriesPoint(
                date=r.indicator_date,
                value=float(r.value),
                change_pct_1d=round(float(r.change_pct_1d), 2)
                if r.change_pct_1d is not None
                else None,
                source_code=r.source_code,
            )
            for r in window_rows
        ]
        if code_rows:
            last = code_rows[-1]
            latest_payload[code.value] = MacroLatestItem(
                indicator_code=code,
                label_vi=_INDICATOR_LABELS_VI[code],
                unit=last.unit,
                date=last.indicator_date,
                value=float(last.value),
                open_value=float(last.open_value) if last.open_value is not None else None,
                high_value=float(last.high_value) if last.high_value is not None else None,
                low_value=float(last.low_value) if last.low_value is not None else None,
                change_pct_1d=round(float(last.change_pct_1d), 2)
                if last.change_pct_1d is not None
                else None,
                change_pct_7d=_compute_7d_change_pct(code_rows),
                source_code=last.source_code,
                source_url=last.source_url,
                metadata=dict(last.metadata_json or {}),
            )

    xau_by_date = {
        r.indicator_date: Decimal(r.value)
        for r in by_code.get(MacroIndicatorCode.XAU_USD.value, [])
    }
    vcb_by_date = {
        r.indicator_date: Decimal(r.value)
        for r in by_code.get(MacroIndicatorCode.USD_VND_VCB.value, [])
    }
    market_by_date = {
        r.indicator_date: Decimal(r.value)
        for r in by_code.get(MacroIndicatorCode.USD_VND_MARKET.value, [])
    }

    sjc_rows = (
        await session.execute(
            select(DailyPriceSeries.price_date, DailyPriceSeries.sell_price_vnd)
            .join(Commodity, DailyPriceSeries.commodity_id == Commodity.id)
            .join(Location, DailyPriceSeries.location_id == Location.id)
            .where(
                Commodity.code == "GOLD_SJC_BAR_SJC",
                Location.code == "HCM",
                DailyPriceSeries.sell_price_vnd.is_not(None),
                DailyPriceSeries.price_date >= start_date,
                DailyPriceSeries.price_date <= end_date,
            )
            .order_by(DailyPriceSeries.price_date.asc())
        )
    ).all()

    spread_series: list[GoldWorldSpreadPoint] = []
    latest_spread_item: GoldWorldSpreadItem | None = None

    for sjc_date, sjc_sell_vnd in sjc_rows:
        if sjc_sell_vnd is None or sjc_sell_vnd <= 0:
            continue
        xau_hit = _find_nearest_prior(sjc_date, xau_by_date, max_lookback_days=5)
        fx_hit = select_fx_rate_for_date(
            sjc_date,
            vcb_by_date=vcb_by_date,
            market_by_date=market_by_date,
            max_lookback_days=5,
        )
        if xau_hit is None or fx_hit is None:
            continue
        xau_val, xau_date = xau_hit
        fx_val, fx_date, fx_source = fx_hit
        spread = compute_sjc_world_gold_spread(
            xau_usd=xau_val,
            usd_vnd=fx_val,
            sjc_sell_vnd=int(sjc_sell_vnd),
            fx_source=fx_source,
            xau_date=xau_date,
            fx_date=fx_date,
            sjc_date=sjc_date,
        )
        spread_series.append(
            GoldWorldSpreadPoint(
                date=sjc_date,
                sjc_sell_vnd=spread.sjc_sell_vnd,
                world_vnd_per_tael=spread.world_vnd_per_tael,
                spread_vnd=spread.spread_vnd,
                spread_pct=spread.spread_pct,
                fx_source=spread.fx_source,
            )
        )
        latest_spread_item = GoldWorldSpreadItem(
            sjc_sell_vnd=spread.sjc_sell_vnd,
            sjc_date=spread.sjc_date,
            xau_usd=spread.xau_usd,
            xau_date=spread.xau_date,
            usd_vnd=spread.usd_vnd,
            fx_date=spread.fx_date,
            fx_source=spread.fx_source,
            conversion_factor=spread.conversion_factor,
            world_vnd_per_tael=spread.world_vnd_per_tael,
            spread_vnd=spread.spread_vnd,
            spread_pct=spread.spread_pct,
            disclaimer_vi=spread.disclaimer_vi,
        )

    return MacroAnalyticsResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=vn_now(),
        range=history_range,
        start_date=start_date,
        end_date=end_date,
        latest=latest_payload,
        sjc_world_spread=latest_spread_item,
        sjc_world_spread_series=spread_series,
        series=series_payload,
    )


async def get_news_analytics(
    session: AsyncSession,
    *,
    domain: NewsDomainFilter,
    hours: int,
    limit: int,
) -> NewsAnalyticsResponse:
    """Return filtered market news signals with deterministic/Gemini summaries."""
    now_vn = vn_now()
    cutoff_vn = now_vn - timedelta(hours=hours)

    stmt = (
        select(MarketNewsSignal)
        .where(MarketNewsSignal.published_at >= cutoff_vn)
        .order_by(MarketNewsSignal.published_at.desc())
        .limit(500)
    )
    if domain != NewsDomainFilter.ALL:
        stmt = stmt.where(MarketNewsSignal.domain == domain.value)

    rows = (await session.execute(stmt)).scalars().all()
    total = len(rows)
    high_count = sum(1 for r in rows if r.impact_level == NewsImpactLevel.HIGH.value)
    avg_polarity = (
        round(sum(float(r.polarity_score) for r in rows) / total, 3) if total > 0 else None
    )

    catalyst_counts: Counter[str] = Counter()
    for r in rows:
        for cat in r.catalysts or []:
            catalyst_counts[str(cat)] += 1
    top_catalysts = [
        cat for cat, _ in sorted(catalyst_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    items = [
        NewsSignalItem(
            id=r.id,
            domain=NewsDomain(r.domain),
            source_name=r.publisher,
            title=r.title,
            url=r.url,
            published_at=r.published_at,
            impact_level=NewsImpactLevel(r.impact_level),
            polarity_score=float(r.polarity_score),
            catalysts=[str(c) for c in (r.catalysts or [])],
            matched_keywords=[str(k) for k in (r.matched_keywords or [])],
            summary=r.llm_summary_vi or r.summary_snippet,
            llm_summary_vi=r.llm_summary_vi,
            scoring_engine=NewsScoringEngine(r.scoring_engine),
        )
        for r in rows[:limit]
    ]

    return NewsAnalyticsResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=now_vn,
        domain_filter=domain,
        hours=hours,
        total=total,
        summary=NewsWindowSummary(
            total_articles=total,
            high_impact_count=high_count,
            avg_polarity_score=avg_polarity,
            top_catalysts=top_catalysts,
        ),
        items=items,
    )

