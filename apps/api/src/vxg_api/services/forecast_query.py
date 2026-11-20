"""Database query services for forecast snapshots, forecast detail view, and forecast audit."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.errors import AppError
from vxg_api.schemas.common import CityCode
from vxg_api.schemas.forecasts import (
    FORECAST_DISCLAIMER_VI,
    AuditEvaluationMetrics,
    AuditForecastEvaluationItem,
    AuditForecastsResponse,
    ForecastBacktestMetrics,
    ForecastDetailResponse,
    ForecastEvaluationTypeFilter,
    ForecastFeatureDriver,
    ForecastItem,
    ForecastNewsRef,
    LatestForecastRunSummary,
    LatestForecastsResponse,
    MacroHighlightCard,
)
from vxg_api.schemas.prices import HistoryLocationCode, HistoryRange
from vxg_api.services.analytics_query import get_macro_analytics
from vxg_api.services.price_query import get_price_history
from vxg_shared.db.models import (
    Commodity,
    ForecastEvaluation,
    ForecastRun,
    Location,
    MarketNewsSignal,
    PriceForecast,
)
from vxg_shared.enums import (
    CommodityCategory,
    CrawlRunStatus,
    CrawlScope,
    ForecastEvaluationType,
    ForecastHorizon,
    ForecastStatus,
    MacroIndicatorCode,
    PredictedDirection,
    PriceUnit,
    PricingType,
    TriggerType,
)
from vxg_shared.timezone import VN_TZ_NAME, vn_now

_HORIZON_ALIASES: dict[str, ForecastHorizon] = {
    "T_PLUS_1": ForecastHorizon.T_PLUS_1,
    "T_PLUS_7": ForecastHorizon.T_PLUS_7,
    "NEXT_FUEL_CYCLE": ForecastHorizon.NEXT_FUEL_CYCLE,
    "NEXT_ADJUSTMENT_CYCLE": ForecastHorizon.NEXT_FUEL_CYCLE,
    "NEXT_GAS_MONTH": ForecastHorizon.NEXT_GAS_MONTH,
    "NEXT_MONTH_CYCLE": ForecastHorizon.NEXT_GAS_MONTH,
}

_DOMAIN_HORIZONS: dict[CommodityCategory, tuple[ForecastHorizon, ...]] = {
    CommodityCategory.GOLD: (ForecastHorizon.T_PLUS_1, ForecastHorizon.T_PLUS_7),
    CommodityCategory.FUEL: (ForecastHorizon.NEXT_FUEL_CYCLE,),
    CommodityCategory.GAS: (ForecastHorizon.NEXT_GAS_MONTH,),
}


def parse_forecast_horizon(raw_horizon: str | None) -> ForecastHorizon | None:
    """Resolve canonical horizon or documented Milestone 9 aliases cleanly."""
    if raw_horizon is None or not raw_horizon.strip():
        return None
    cleaned = raw_horizon.strip().upper()
    horizon = _HORIZON_ALIASES.get(cleaned)
    if horizon is None:
        valid = ", ".join(sorted(_HORIZON_ALIASES))
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_FORECAST_HORIZON",
            message=f"Unsupported forecast horizon '{cleaned}'. Valid values: {valid}.",
        )
    return horizon


def _parse_backtest_metrics(drivers: dict[str, Any]) -> ForecastBacktestMetrics | None:
    raw = drivers.get("backtest")
    if not isinstance(raw, dict):
        return None
    sample_count = int(raw.get("sample_count") or 0)
    if sample_count <= 0:
        return None
    return ForecastBacktestMetrics(
        sample_count=sample_count,
        mae_vnd=int(raw["mae_vnd"]) if raw.get("mae_vnd") is not None else None,
        mape_pct=float(raw["mape_pct"]) if raw.get("mape_pct") is not None else None,
        naive_mae_vnd=int(raw["naive_mae_vnd"]) if raw.get("naive_mae_vnd") is not None else None,
        naive_mape_pct=float(raw["naive_mape_pct"]) if raw.get("naive_mape_pct") is not None else None,
        directional_accuracy_pct=(
            float(raw["directional_accuracy_pct"])
            if raw.get("directional_accuracy_pct") is not None
            else None
        ),
        interval_coverage_pct=(
            float(raw["interval_coverage_pct"])
            if raw.get("interval_coverage_pct") is not None
            else None
        ),
    )


def _parse_drivers(drivers: dict[str, Any]) -> list[ForecastFeatureDriver]:
    raw_list = drivers.get("feature_drivers")
    if not isinstance(raw_list, list):
        return []
    out: list[ForecastFeatureDriver] = []
    for item in raw_list:
        if not isinstance(item, dict) or "key" not in item:
            continue
        out.append(
            ForecastFeatureDriver(
                key=str(item["key"]),
                label_vi=str(item.get("label_vi") or item["key"]),
                value=float(item["value"]) if item.get("value") is not None else None,
                weight_pct=float(item.get("weight_pct") or 0.0),
                direction=str(item.get("direction") or "FLAT"),
            )
        )
    return out


def _parse_news_refs(drivers: dict[str, Any]) -> list[ForecastNewsRef]:
    raw_list = drivers.get("news_signals")
    if not isinstance(raw_list, list):
        return []
    out: list[ForecastNewsRef] = []
    for item in raw_list:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        out.append(
            ForecastNewsRef(
                id=str(item.get("id") or ""),
                domain=str(item.get("domain") or ""),
                publisher=str(item.get("publisher") or ""),
                title=str(item.get("title") or ""),
                url=str(item["url"]),
                published_at_vn=(
                    str(item["published_at_vn"]) if item.get("published_at_vn") else None
                ),
                impact_level=str(item.get("impact_level") or "LOW"),
                polarity_score=float(item.get("polarity_score") or 0.0),
                catalysts=[str(c) for c in (item.get("catalysts") or [])],
                summary=str(item["summary"]) if item.get("summary") else None,
            )
        )
    return out


def _to_forecast_item(
    pf: PriceForecast,
    commodity: Commodity,
    loc: Location,
    *,
    applied_zone: str | None,
) -> ForecastItem:
    drivers = dict(pf.drivers_json or {})
    return ForecastItem(
        forecast_id=pf.id,
        domain=CommodityCategory(commodity.category),
        commodity_code=commodity.code,
        commodity_name=commodity.product_name,
        brand=commodity.brand,
        unit=PriceUnit(commodity.unit),
        pricing_type=PricingType(commodity.pricing_type),
        location_code=loc.code,
        location_name=loc.name,
        applied_zone=applied_zone,
        horizon_type=ForecastHorizon(pf.horizon_type),
        model_version=pf.model_version,
        status=ForecastStatus(pf.status),
        insufficient_reason=pf.insufficient_reason,
        forecast_date=pf.forecast_date,
        target_date=pf.target_date,
        baseline_price=pf.baseline_price_vnd,
        point_estimate=pf.point_estimate_vnd,
        lower_bound=pf.lower_bound_vnd,
        upper_bound=pf.upper_bound_vnd,
        expected_change_vnd=pf.expected_change_vnd,
        expected_change_pct=(
            float(pf.expected_change_pct) if pf.expected_change_pct is not None else None
        ),
        predicted_direction=PredictedDirection(pf.predicted_direction),
        confidence_score=float(pf.confidence_score) if pf.confidence_score is not None else None,
        news_adjustment_pct=float(pf.news_adjustment_pct),
        high_volatility_flag=bool(drivers.get("high_volatility_flag", False)),
        band_widening_pct=float(drivers.get("band_widening_pct") or 0.0),
        feature_cutoff_vn=(
            str(drivers["feature_cutoff_vn"]) if drivers.get("feature_cutoff_vn") else None
        ),
        backtest_summary=_parse_backtest_metrics(drivers),
        feature_drivers=_parse_drivers(drivers),
        news_signals=_parse_news_refs(drivers),
        drivers_json=drivers,
    )


async def get_latest_forecasts(
    session: AsyncSession,
    *,
    domain: CommodityCategory | None,
    location: CityCode,
    horizon: ForecastHorizon | None,
) -> LatestForecastsResponse:
    """Return latest stored forecast snapshots filtered by domain, city, and horizon."""
    city_row = (
        await session.execute(select(Location).where(Location.code == location.value))
    ).scalar_one_or_none()
    if city_row is None:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="LOCATION_NOT_FOUND",
            message=f"Location '{location.value}' was not found.",
        )

    fuel_zone_code = city_row.fuel_zone_code or "VUNG_1"
    fuel_zone_row = (
        await session.execute(select(Location).where(Location.code == fuel_zone_code))
    ).scalar_one_or_none()

    target_loc_ids = [int(city_row.id)]
    if fuel_zone_row is not None and (domain is None or domain == CommodityCategory.FUEL):
        target_loc_ids.append(int(fuel_zone_row.id))

    stmt = (
        select(PriceForecast, Commodity, Location)
        .join(Commodity, PriceForecast.commodity_id == Commodity.id)
        .join(Location, PriceForecast.location_id == Location.id)
        .where(
            Commodity.is_active.is_(True),
            PriceForecast.location_id.in_(target_loc_ids),
        )
        .order_by(
            PriceForecast.commodity_id.asc(),
            PriceForecast.location_id.asc(),
            PriceForecast.horizon_type.asc(),
            PriceForecast.forecast_date.desc(),
            PriceForecast.created_at.desc(),
        )
    )
    if domain is not None:
        stmt = stmt.where(Commodity.category == domain.value)
    if horizon is not None:
        stmt = stmt.where(PriceForecast.horizon_type == horizon.value)

    rows = (await session.execute(stmt)).all()
    seen_keys: set[tuple[int, int, str]] = set()
    items: list[ForecastItem] = []

    for pf, commodity, loc in rows:
        is_fuel = commodity.category == CommodityCategory.FUEL.value
        if is_fuel and fuel_zone_row is not None and loc.id != fuel_zone_row.id:
            continue
        if not is_fuel and loc.id != city_row.id:
            continue
        key = (int(pf.commodity_id), int(pf.location_id), pf.horizon_type)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(
            _to_forecast_item(
                pf,
                commodity,
                city_row,
                applied_zone=fuel_zone_code if is_fuel else None,
            )
        )

    domain_order = {"GOLD": 0, "FUEL": 1, "GAS": 2}
    items.sort(
        key=lambda i: (
            0 if i.status == ForecastStatus.READY else 1,
            domain_order.get(i.domain.value, 9),
            i.commodity_code,
            i.horizon_type.value,
        )
    )

    return LatestForecastsResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=vn_now(),
        domain_filter=domain,
        location_filter=location.value,
        applied_zone=fuel_zone_code if domain in (None, CommodityCategory.FUEL) else None,
        disclaimer=FORECAST_DISCLAIMER_VI,
        count=len(items),
        items=items,
    )


async def get_forecast_detail(
    session: AsyncSession,
    *,
    commodity_code: str,
    location: HistoryLocationCode,
    horizon: ForecastHorizon | None,
    history_range: HistoryRange,
) -> ForecastDetailResponse:
    """Return full forecast snapshot, history chart series, 3 macro highlights, and news."""
    history = await get_price_history(
        session,
        commodity_code=commodity_code,
        location=location,
        history_range=history_range,
    )
    domain = history.domain
    allowed_horizons = list(_DOMAIN_HORIZONS[domain])
    selected_horizon = horizon or allowed_horizons[0]
    if selected_horizon not in allowed_horizons:
        valid_str = ", ".join(h.value for h in allowed_horizons)
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_FORECAST_HORIZON",
            message=(
                f"Horizon '{selected_horizon.value}' is not valid for {domain.value}. "
                f"Expected: {valid_str}."
            ),
        )

    commodity = (
        await session.execute(select(Commodity).where(Commodity.code == history.commodity_code))
    ).scalar_one()
    target_loc_code = history.applied_zone or history.location_code
    target_loc = (
        await session.execute(select(Location).where(Location.code == target_loc_code))
    ).scalar_one()
    display_loc = (
        await session.execute(select(Location).where(Location.code == history.location_code))
    ).scalar_one()

    pf = (
        await session.execute(
            select(PriceForecast)
            .where(
                PriceForecast.commodity_id == commodity.id,
                PriceForecast.location_id == target_loc.id,
                PriceForecast.horizon_type == selected_horizon.value,
            )
            .order_by(PriceForecast.forecast_date.desc(), PriceForecast.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if pf is not None:
        forecast_item = _to_forecast_item(
            pf,
            commodity,
            display_loc,
            applied_zone=history.applied_zone,
        )
    else:
        today_vn = vn_now().date()
        forecast_item = ForecastItem(
            forecast_id=None,
            domain=domain,
            commodity_code=commodity.code,
            commodity_name=commodity.product_name,
            brand=commodity.brand,
            unit=PriceUnit(commodity.unit),
            pricing_type=PricingType(commodity.pricing_type),
            location_code=display_loc.code,
            location_name=display_loc.name,
            applied_zone=history.applied_zone,
            horizon_type=selected_horizon,
            model_version="pending",
            status=ForecastStatus.INSUFFICIENT_DATA,
            insufficient_reason="Chưa có bản ghi dự báo nào được sinh cho mặt hàng này.",
            forecast_date=today_vn,
            target_date=today_vn + timedelta(days=1),
            baseline_price=history.points[-1].reference_price if history.points else None,
            predicted_direction=PredictedDirection.UNKNOWN,
            feature_drivers=[],
            news_signals=[],
            drivers_json={},
        )

    macro = await get_macro_analytics(session, indicators=None, history_range=HistoryRange.D30)
    highlights: list[MacroHighlightCard] = []

    def _add_macro_card(code: MacroIndicatorCode, label_override: str | None = None) -> None:
        item = macro.latest.get(code.value)
        if item is None:
            return
        unit_suffix = "$" if "USD" in item.unit and "VND" not in item.unit else ""
        val_disp = (
            f"{item.value:,.0f} VND"
            if "VND" in item.unit
            else (f"{unit_suffix}{item.value:,.2f}".strip())
        )
        highlights.append(
            MacroHighlightCard(
                key=code.value,
                label_vi=label_override or item.label_vi,
                value_display=val_disp,
                value=item.value,
                unit=item.unit,
                change_pct_1d=item.change_pct_1d,
                change_pct_7d=item.change_pct_7d,
                note_vi=f"Nguồn: {item.source_code} ({item.date.isoformat()})",
            )
        )

    fx_code = (
        MacroIndicatorCode.USD_VND_VCB
        if MacroIndicatorCode.USD_VND_VCB.value in macro.latest
        else MacroIndicatorCode.USD_VND_MARKET
    )
    if domain == CommodityCategory.GOLD:
        _add_macro_card(MacroIndicatorCode.XAU_USD, "Vàng thế giới (XAU/USD)")
        _add_macro_card(fx_code, "Tỷ giá USD/VND")
        if macro.sjc_world_spread is not None:
            sp = macro.sjc_world_spread
            highlights.append(
                MacroHighlightCard(
                    key="SJC_WORLD_SPREAD",
                    label_vi="Chênh lệch SJC / Thế giới quy đổi",
                    value_display=f"{sp.spread_pct:+.2f}%",
                    value=sp.spread_pct,
                    unit="PCT",
                    note_vi=f"Quy đổi {sp.world_vnd_per_tael:,} VND/lượng ({sp.fx_source})",
                )
            )
        else:
            _add_macro_card(MacroIndicatorCode.DXY, "Chỉ số USD Index (DXY)")
    else:
        _add_macro_card(MacroIndicatorCode.BRENT_USD, "Dầu thô Brent (USD/thùng)")
        _add_macro_card(MacroIndicatorCode.WTI_USD, "Dầu thô WTI (USD/thùng)")
        _add_macro_card(fx_code, "Tỷ giá USD/VND")

    news_signals = list(forecast_item.news_signals)
    if not news_signals:
        cutoff_news = vn_now() - timedelta(hours=336 if domain == CommodityCategory.GAS else 72)
        news_rows = (
            await session.execute(
                select(MarketNewsSignal)
                .where(
                    MarketNewsSignal.domain == domain.value,
                    MarketNewsSignal.published_at >= cutoff_news,
                )
                .order_by(MarketNewsSignal.published_at.desc())
                .limit(5)
            )
        ).scalars().all()
        news_signals = [
            ForecastNewsRef(
                id=str(n.id),
                domain=n.domain,
                publisher=n.publisher,
                title=n.title,
                url=n.url,
                published_at_vn=n.published_at.isoformat(),
                impact_level=n.impact_level,
                polarity_score=float(n.polarity_score),
                catalysts=[str(c) for c in (n.catalysts or [])],
                summary=n.llm_summary_vi or n.summary_snippet,
            )
            for n in news_rows
        ]

    return ForecastDetailResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=vn_now(),
        disclaimer=FORECAST_DISCLAIMER_VI,
        commodity_code=commodity.code,
        location_code=display_loc.code,
        applied_zone=history.applied_zone,
        available_horizons=allowed_horizons,
        forecast=forecast_item,
        history_range=history_range,
        history_stats=history.stats,
        history_points=history.points,
        macro_highlights=highlights[:3],
        macro_drivers=forecast_item.feature_drivers,
        news_signals=news_signals[:5],
        backtest_summary=forecast_item.backtest_summary,
    )


def _aggregate_eval_metrics(
    eval_type: ForecastEvaluationType,
    rows: list[ForecastEvaluation],
) -> AuditEvaluationMetrics:
    total = len(rows)
    if total == 0:
        return AuditEvaluationMetrics(evaluation_type=eval_type, total_evaluated=0)

    mae = int(round(sum(r.abs_error_vnd for r in rows) / total))
    mape = round(sum(float(r.pct_error) for r in rows) / total, 3)
    naive_mae = int(round(sum(abs(r.baseline_price_vnd - r.actual_price_vnd) for r in rows) / total))
    naive_mape = round(
        sum(
            abs(r.baseline_price_vnd - r.actual_price_vnd) / float(Decimal(r.actual_price_vnd)) * 100.0
            for r in rows
            if r.actual_price_vnd > 0
        )
        / total,
        3,
    )
    dir_acc = round(sum(1 for r in rows if r.direction_correct) / total * 100.0, 1)
    cov = round(sum(1 for r in rows if r.within_interval) / total * 100.0, 1)

    return AuditEvaluationMetrics(
        evaluation_type=eval_type,
        total_evaluated=total,
        mae_vnd=mae,
        mape_pct=mape,
        naive_mae_vnd=naive_mae,
        naive_mape_pct=naive_mape,
        direction_accuracy_pct=dir_acc,
        interval_coverage_pct=cov,
    )


async def get_audit_forecasts(
    session: AsyncSession,
    *,
    evaluation_type: ForecastEvaluationTypeFilter,
    domain: CommodityCategory | None,
    horizon: ForecastHorizon | None,
    limit: int,
    offset: int,
) -> AuditForecastsResponse:
    """Return strictly separated LIVE vs BACKTEST evaluation summaries and paginated rows."""
    stmt = (
        select(ForecastEvaluation, Commodity, Location)
        .join(Commodity, ForecastEvaluation.commodity_id == Commodity.id)
        .join(Location, ForecastEvaluation.location_id == Location.id)
        .order_by(ForecastEvaluation.target_date.desc(), ForecastEvaluation.evaluated_at.desc())
    )
    if domain is not None:
        stmt = stmt.where(Commodity.category == domain.value)
    if horizon is not None:
        stmt = stmt.where(ForecastEvaluation.horizon_type == horizon.value)

    all_rows = (await session.execute(stmt)).all()

    live_evals = [
        ev
        for ev, _, _ in all_rows
        if ev.evaluation_type == ForecastEvaluationType.LIVE.value
    ]
    backtest_evals = [
        ev
        for ev, _, _ in all_rows
        if ev.evaluation_type == ForecastEvaluationType.BACKTEST.value
    ]

    if evaluation_type == ForecastEvaluationTypeFilter.ALL:
        filtered = all_rows
    else:
        filtered = [
            row
            for row in all_rows
            if row[0].evaluation_type == evaluation_type.value
        ]

    total = len(filtered)
    sliced = filtered[offset : offset + limit]

    items = [
        AuditForecastEvaluationItem(
            evaluation_id=ev.id,
            evaluation_type=ForecastEvaluationType(ev.evaluation_type),
            forecast_id=ev.forecast_id,
            domain=CommodityCategory(commodity.category),
            commodity_code=commodity.code,
            commodity_name=commodity.product_name,
            location_code=loc.code,
            horizon_type=ForecastHorizon(ev.horizon_type),
            model_version=ev.model_version,
            forecast_date=ev.forecast_date,
            target_date=ev.target_date,
            actual_date=ev.actual_date,
            baseline_price=ev.baseline_price_vnd,
            predicted_price=ev.predicted_price_vnd,
            lower_bound=ev.lower_bound_vnd,
            upper_bound=ev.upper_bound_vnd,
            actual_price=ev.actual_price_vnd,
            abs_error_vnd=ev.abs_error_vnd,
            pct_error=float(ev.pct_error),
            predicted_direction=PredictedDirection(ev.predicted_direction),
            actual_direction=PredictedDirection(ev.actual_direction),
            direction_correct=bool(ev.direction_correct),
            within_interval=bool(ev.within_interval),
            evaluated_at_vn=ev.evaluated_at,
        )
        for ev, commodity, loc in sliced
    ]

    latest_run_row = (
        await session.execute(select(ForecastRun).order_by(ForecastRun.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    latest_run = (
        LatestForecastRunSummary(
            id=latest_run_row.id,
            run_code=latest_run_row.run_code,
            scope_category=CrawlScope(latest_run_row.scope_category),
            trigger_type=TriggerType(latest_run_row.trigger_type),
            status=CrawlRunStatus(latest_run_row.status),
            forecasts_generated=latest_run_row.forecasts_generated,
            evaluations_created=latest_run_row.evaluations_created,
            started_at_vn=latest_run_row.started_at,
            finished_at_vn=latest_run_row.finished_at,
            duration_ms=latest_run_row.duration_ms,
            error_summary=latest_run_row.error_summary,
        )
        if latest_run_row is not None
        else None
    )

    return AuditForecastsResponse(
        timezone=VN_TZ_NAME,
        as_of_vn=vn_now(),
        evaluation_type_filter=evaluation_type,
        domain_filter=domain,
        horizon_filter=horizon,
        live_summary=_aggregate_eval_metrics(ForecastEvaluationType.LIVE, live_evals),
        backtest_summary=_aggregate_eval_metrics(ForecastEvaluationType.BACKTEST, backtest_evals),
        latest_forecast_run=latest_run,
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )

