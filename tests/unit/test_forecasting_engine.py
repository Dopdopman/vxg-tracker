"""Unit tests for Milestone 9 forecasting engine: Zero Look-Ahead, Cycle Collapsing & Metrics."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from vxg_api.config import ApiSettings
from vxg_api.services.admin_service import dispatch_github_workflow
from vxg_shared.enums import (
    CommodityCategory,
    CrawlScope,
    ForecastHorizon,
    ForecastStatus,
    MacroIndicatorCode,
    NewsDomain,
    NewsImpactLevel,
    PredictedDirection,
    SeriesOrigin,
)
from vxg_shared.timezone import VN_TZ
from vxg_worker.forecasting.calendar import (
    next_fuel_adjustment_date,
    next_gas_month_date,
    next_gold_target_date,
)
from vxg_worker.forecasting.features import (
    collapse_fuel_adjustment_cycles,
    collapse_gas_monthly_cycles,
)
from vxg_worker.forecasting.fuel_model import FUEL_MODEL_VERSION, generate_fuel_forecast
from vxg_worker.forecasting.gas_model import (
    GAS_PASSTHROUGH_VERSION,
    GAS_RIDGE_VERSION,
    generate_gas_forecast,
)
from vxg_worker.forecasting.gold_model import GOLD_MODEL_VERSION, generate_gold_forecast
from vxg_worker.forecasting.metrics import (
    build_backtest_point,
    summarize_walk_forward,
)
from vxg_worker.forecasting.news_overlay import apply_news_risk_overlay
from vxg_worker.forecasting.types import MacroSeriesBundle, NewsObs, PriceObs

_API_TOML = Path(__file__).resolve().parents[2] / "apps/api/pyproject.toml"
_SHARED_TOML = Path(__file__).resolve().parents[2] / "packages/shared-py/pyproject.toml"


def _build_synthetic_inputs(
    as_of: date,
    *,
    gold_days: int = 100,
    fuel_cycles: int = 20,
    gas_months: int = 8,
    include_future_shock: bool = False,
) -> tuple[
    list[PriceObs],
    list[PriceObs],
    list[PriceObs],
    MacroSeriesBundle,
    list[NewsObs],
]:
    gold_prices: list[PriceObs] = []
    macro_by_code: dict[str, dict[date, Decimal]] = {
        MacroIndicatorCode.XAU_USD.value: {},
        MacroIndicatorCode.BRENT_USD.value: {},
        MacroIndicatorCode.WTI_USD.value: {},
        MacroIndicatorCode.DXY.value: {},
        MacroIndicatorCode.USD_VND_VCB.value: {},
        MacroIndicatorCode.USD_VND_MARKET.value: {},
    }

    total_macro_days = max(gold_days + 40, fuel_cycles * 8 + 40, gas_months * 32 + 40)
    for offset in range(total_macro_days, -1, -1):
        d = as_of - timedelta(days=offset)
        wave = (offset % 9) - 4
        macro_by_code[MacroIndicatorCode.XAU_USD.value][d] = Decimal(str(3500 + offset * 0.8 + wave * 6))
        macro_by_code[MacroIndicatorCode.BRENT_USD.value][d] = Decimal(str(78.0 + (offset % 14) * 0.35))
        macro_by_code[MacroIndicatorCode.WTI_USD.value][d] = Decimal(str(74.0 + (offset % 14) * 0.32))
        macro_by_code[MacroIndicatorCode.DXY.value][d] = Decimal(str(103.5 - (offset % 7) * 0.15))
        macro_by_code[MacroIndicatorCode.USD_VND_VCB.value][d] = Decimal(str(25900 + (offset % 5) * 15))
        macro_by_code[MacroIndicatorCode.USD_VND_MARKET.value][d] = Decimal(str(25650 + (offset % 5) * 12))

    for offset in range(gold_days - 1, -1, -1):
        d = as_of - timedelta(days=offset)
        base_vnd = 128_000_000 + ((gold_days - offset) * 70_000) + (((offset % 5) - 2) * 300_000)
        gold_prices.append(
            PriceObs(
                price_date=d,
                reference_price_vnd=base_vnd,
                is_adjustment_day=(offset % 2 == 0),
                series_origin=SeriesOrigin.BACKFILL.value,
                source_code="CAFEF_GOLD_BACKFILL",
            )
        )

    fuel_prices: list[PriceObs] = []
    for k in range(fuel_cycles - 1, -1, -1):
        d = as_of - timedelta(days=k * 7)
        p_vnd = 19_500 + ((fuel_cycles - k) % 6 - 2) * 260
        fuel_prices.append(
            PriceObs(
                price_date=d,
                reference_price_vnd=p_vnd,
                is_adjustment_day=True,
                series_origin=SeriesOrigin.BACKFILL.value,
                source_code="SAIGONPETRO_FUEL_BACKFILL",
            )
        )

    gas_prices: list[PriceObs] = []
    for m in range(gas_months - 1, -1, -1):
        d = as_of - timedelta(days=m * 30)
        gas_prices.append(
            PriceObs(
                price_date=d,
                reference_price_vnd=450_000 + ((gas_months - m) % 4) * 12_000,
                is_adjustment_day=True,
                series_origin=SeriesOrigin.BACKFILL.value,
                source_code="SAIGONPETRO_GAS_BACKFILL",
            )
        )

    news = [
        NewsObs(
            id="past-gold-1",
            domain=NewsDomain.GOLD.value,
            publisher="VnExpress",
            title="Fed giữ lập trường thận trọng với lãi suất",
            url="https://vnexpress.net/gold-past-1.html",
            published_at_vn=datetime(as_of.year, as_of.month, as_of.day, 10, 0, tzinfo=VN_TZ)
            - timedelta(hours=4),
            polarity_score=Decimal("0.650"),
            impact_level=NewsImpactLevel.HIGH.value,
            catalysts=["FED_RATE"],
            summary="Tác động ngắn hạn tới vàng.",
        ),
        NewsObs(
            id="past-gas-1",
            domain=NewsDomain.GAS.value,
            publisher="CafeF",
            title="Saudi Aramco công bố giá CP giữ ổn định",
            url="https://cafef.vn/gas-past-1.html",
            published_at_vn=datetime(as_of.year, as_of.month, as_of.day, 9, 0, tzinfo=VN_TZ)
            - timedelta(days=2),
            polarity_score=Decimal("0.400"),
            impact_level=NewsImpactLevel.MEDIUM.value,
            catalysts=["CP_ARAMCO"],
            summary="Giá CP thế giới tăng nhẹ.",
        ),
    ]

    if include_future_shock:
        # Inject massive +500% shocks on all days > as_of (must NEVER affect forecasts at as_of!)
        for fut in range(1, 15):
            fd = as_of + timedelta(days=fut)
            gold_prices.append(
                PriceObs(fd, 750_000_000, True, SeriesOrigin.LIVE_ROLLUP.value, "FUTURE_SHOCK")
            )
            fuel_prices.append(
                PriceObs(fd, 120_000, True, SeriesOrigin.LIVE_ROLLUP.value, "FUTURE_SHOCK")
            )
            gas_prices.append(
                PriceObs(fd, 3_500_000, True, SeriesOrigin.LIVE_ROLLUP.value, "FUTURE_SHOCK")
            )
            macro_by_code[MacroIndicatorCode.XAU_USD.value][fd] = Decimal("9999")
            macro_by_code[MacroIndicatorCode.BRENT_USD.value][fd] = Decimal("240")
            macro_by_code[MacroIndicatorCode.WTI_USD.value][fd] = Decimal("230")
            macro_by_code[MacroIndicatorCode.DXY.value][fd] = Decimal("135")
            macro_by_code[MacroIndicatorCode.USD_VND_VCB.value][fd] = Decimal("34000")
            macro_by_code[MacroIndicatorCode.USD_VND_MARKET.value][fd] = Decimal("34000")

        news.append(
            NewsObs(
                id="future-shock-news",
                domain=NewsDomain.GOLD.value,
                publisher="Test",
                title="Tin sốc tương lai sau giờ cutoff",
                url="https://example.test/future-shock.html",
                published_at_vn=datetime(as_of.year, as_of.month, as_of.day, 12, 0, tzinfo=VN_TZ)
                + timedelta(seconds=1),
                polarity_score=Decimal("-1.000"),
                impact_level=NewsImpactLevel.HIGH.value,
                catalysts=["FED_RATE", "GEOPOLITICAL"],
                summary="Future leakage probe.",
            )
        )

    return gold_prices, fuel_prices, gas_prices, MacroSeriesBundle(by_code=macro_by_code), news


def test_zero_lookahead_bias_all_three_domains() -> None:
    as_of = date(2026, 11, 20)
    as_of_dt = datetime(2026, 11, 20, 12, 0, tzinfo=VN_TZ)

    g_base, f_base, gas_base, m_base, n_base = _build_synthetic_inputs(as_of, include_future_shock=False)
    g_shock, f_shock, gas_shock, m_shock, n_shock = _build_synthetic_inputs(as_of, include_future_shock=True)

    pairs_base = [
        generate_gold_forecast(
            commodity_code="GOLD_SJC_BAR_SJC",
            location_code="HCM",
            horizon=ForecastHorizon.T_PLUS_1,
            as_of_dt_vn=as_of_dt,
            price_series=g_base,
            macro_bundle=m_base,
            news_items=n_base,
        ),
        generate_gold_forecast(
            commodity_code="GOLD_SJC_BAR_SJC",
            location_code="HCM",
            horizon=ForecastHorizon.T_PLUS_7,
            as_of_dt_vn=as_of_dt,
            price_series=g_base,
            macro_bundle=m_base,
            news_items=n_base,
        ),
        generate_fuel_forecast(
            commodity_code="FUEL_RON95_III",
            location_code="VUNG_1",
            as_of_dt_vn=as_of_dt,
            price_series=f_base,
            macro_bundle=m_base,
            news_items=n_base,
        ),
        generate_gas_forecast(
            commodity_code="GAS_SAIGONPETRO_12KG",
            location_code="HCM",
            as_of_dt_vn=as_of_dt,
            price_series=gas_base,
            macro_bundle=m_base,
            news_items=n_base,
        ),
    ]
    pairs_shock = [
        generate_gold_forecast(
            commodity_code="GOLD_SJC_BAR_SJC",
            location_code="HCM",
            horizon=ForecastHorizon.T_PLUS_1,
            as_of_dt_vn=as_of_dt,
            price_series=g_shock,
            macro_bundle=m_shock,
            news_items=n_shock,
        ),
        generate_gold_forecast(
            commodity_code="GOLD_SJC_BAR_SJC",
            location_code="HCM",
            horizon=ForecastHorizon.T_PLUS_7,
            as_of_dt_vn=as_of_dt,
            price_series=g_shock,
            macro_bundle=m_shock,
            news_items=n_shock,
        ),
        generate_fuel_forecast(
            commodity_code="FUEL_RON95_III",
            location_code="VUNG_1",
            as_of_dt_vn=as_of_dt,
            price_series=f_shock,
            macro_bundle=m_shock,
            news_items=n_shock,
        ),
        generate_gas_forecast(
            commodity_code="GAS_SAIGONPETRO_12KG",
            location_code="HCM",
            as_of_dt_vn=as_of_dt,
            price_series=gas_shock,
            macro_bundle=m_shock,
            news_items=n_shock,
        ),
    ]

    for b_out, s_out in zip(pairs_base, pairs_shock, strict=True):
        assert b_out.status == ForecastStatus.READY
        assert b_out.point_estimate_vnd == s_out.point_estimate_vnd
        assert b_out.lower_bound_vnd == s_out.lower_bound_vnd
        assert b_out.upper_bound_vnd == s_out.upper_bound_vnd
        assert b_out.expected_change_pct == s_out.expected_change_pct
        assert b_out.confidence_score == s_out.confidence_score
        assert b_out.drivers_json == s_out.drivers_json


def test_gas_monthly_cycle_collapsing_and_model_regime() -> None:
    # 35 noisy VietNamBiz daily flat rows inside 8 months MUST collapse to 8 monthly points
    noisy_rows: list[PriceObs] = []
    start_dates = [
        date(2026, 4, 27),
        date(2026, 5, 1),
        date(2026, 6, 1),
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    ]
    for m_idx, d0 in enumerate(start_dates):
        for offset in range(4):
            noisy_rows.append(
                PriceObs(
                    price_date=d0 + timedelta(days=offset),
                    reference_price_vnd=450_000 + m_idx * 10_000,
                    is_adjustment_day=(offset == 0),
                    series_origin=SeriesOrigin.BACKFILL.value,
                    source_code="VIETNAMBIZ_GAS_BACKFILL",
                )
            )
    collapsed = collapse_gas_monthly_cycles(noisy_rows)
    assert len(collapsed) == 8
    assert [(p.price_date.year, p.price_date.month) for p in collapsed] == [
        (2026, 4),
        (2026, 5),
        (2026, 6),
        (2026, 7),
        (2026, 8),
        (2026, 9),
        (2026, 10),
        (2026, 11),
    ]

    as_of_dt = datetime(2026, 11, 20, 12, 0, tzinfo=VN_TZ)
    _, _, _, macro_bundle, news = _build_synthetic_inputs(as_of_dt.date(), gas_months=15)

    # 8 months -> Pass-Through regime (NEVER Ridge overfitting)
    out_8m = generate_gas_forecast(
        commodity_code="GAS_PETROLIMEX_12KG",
        location_code="HCM",
        as_of_dt_vn=as_of_dt,
        price_series=noisy_rows,
        macro_bundle=macro_bundle,
        news_items=news,
    )
    assert out_8m.status == ForecastStatus.READY
    assert out_8m.model_version == GAS_PASSTHROUGH_VERSION
    assert out_8m.confidence_score is not None and out_8m.confidence_score <= Decimal("0.620")
    assert out_8m.drivers_json["collapsed_monthly_cycles"] == 8
    assert out_8m.drivers_json["raw_daily_rows"] == 32

    # 14 distinct months -> upgrades cleanly to Ridge
    _, _, gas_14m, _, _ = _build_synthetic_inputs(as_of_dt.date(), gas_months=14)
    out_14m = generate_gas_forecast(
        commodity_code="GAS_SAIGONPETRO_12KG",
        location_code="HCM",
        as_of_dt_vn=as_of_dt,
        price_series=gas_14m,
        macro_bundle=macro_bundle,
        news_items=news,
    )
    assert out_14m.status == ForecastStatus.READY
    assert out_14m.model_version == GAS_RIDGE_VERSION


def test_fuel_calendar_cycle_collapsing_and_insufficient_data() -> None:
    # Thursday before 15:00 -> same Thursday; at/after 15:00 -> +7d
    assert next_fuel_adjustment_date(datetime(2026, 11, 19, 14, 59, tzinfo=VN_TZ)) == date(2026, 11, 19)
    assert next_fuel_adjustment_date(datetime(2026, 11, 19, 15, 0, tzinfo=VN_TZ)) == date(2026, 11, 26)
    assert next_fuel_adjustment_date(datetime(2026, 11, 20, 12, 0, tzinfo=VN_TZ)) == date(2026, 11, 26)
    assert next_fuel_adjustment_date(datetime(2026, 11, 18, 20, 0, tzinfo=VN_TZ)) == date(2026, 11, 19)

    assert next_gold_target_date(date(2026, 11, 20), ForecastHorizon.T_PLUS_1) == date(2026, 11, 21)
    assert next_gold_target_date(date(2026, 11, 20), ForecastHorizon.T_PLUS_7) == date(2026, 11, 27)
    assert next_gas_month_date(date(2026, 12, 15)) == date(2027, 1, 1)

    # Flat non-adjustment fuel rows between Thursdays must collapse out
    flat_midweek = [
        PriceObs(date(2026, 11, 5), 20_000, True, "BACKFILL", "SAIGONPETRO_FUEL"),
        PriceObs(date(2026, 11, 6), 20_000, False, "LIVE_ROLLUP", "PETROLIMEX_FUEL"),
        PriceObs(date(2026, 11, 7), 20_000, False, "LIVE_ROLLUP", "PETROLIMEX_FUEL"),
        PriceObs(date(2026, 11, 12), 19_800, True, "LIVE_ROLLUP", "PETROLIMEX_FUEL"),
    ]
    assert [p.price_date for p in collapse_fuel_adjustment_cycles(flat_midweek)] == [
        date(2026, 11, 5),
        date(2026, 11, 12),
    ]

    as_of_dt = datetime(2026, 11, 20, 12, 0, tzinfo=VN_TZ)
    empty_macro = MacroSeriesBundle(by_code={})
    gold_insuff = generate_gold_forecast(
        commodity_code="GOLD_SJC_BAR_DOJI",
        location_code="HCM",
        horizon=ForecastHorizon.T_PLUS_1,
        as_of_dt_vn=as_of_dt,
        price_series=[PriceObs(date(2026, 11, 19), 134_000_000, True, "LIVE_ROLLUP", "DOJI")],
        macro_bundle=empty_macro,
        news_items=[],
    )
    assert gold_insuff.status == ForecastStatus.INSUFFICIENT_DATA
    assert gold_insuff.model_version == GOLD_MODEL_VERSION
    assert gold_insuff.point_estimate_vnd is None
    assert gold_insuff.predicted_direction == PredictedDirection.UNKNOWN
    assert "1/60 ngày" in (gold_insuff.insufficient_reason or "")

    fuel_insuff = generate_fuel_forecast(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_2",
        as_of_dt_vn=as_of_dt,
        price_series=collapse_fuel_adjustment_cycles(flat_midweek),
        macro_bundle=empty_macro,
        news_items=[],
    )
    assert fuel_insuff.status == ForecastStatus.INSUFFICIENT_DATA
    assert fuel_insuff.model_version == FUEL_MODEL_VERSION
    assert "2/12 kỳ" in (fuel_insuff.insufficient_reason or "")


def test_news_overlay_widening_caps_and_walk_forward_metrics() -> None:
    cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=VN_TZ)
    news = [
        NewsObs(
            id=f"hi-{idx}",
            domain=NewsDomain.FUEL.value,
            publisher="VnExpress",
            title=f"OPEC+ cắt giảm sản lượng #{idx}",
            url=f"https://vnexpress.net/opec-{idx}.html",
            published_at_vn=cutoff - timedelta(hours=idx + 1),
            polarity_score=Decimal("-0.700"),
            impact_level=NewsImpactLevel.HIGH.value,
            catalysts=["OPEC_SUPPLY"],
            summary="Rủi ro biến động nguồn cung dầu.",
        )
        for idx in range(3)
    ]

    overlay = apply_news_risk_overlay(
        domain=CommodityCategory.FUEL,
        point_estimate_vnd=20_000,
        lower_bound_vnd=19_500,
        upper_bound_vnd=20_500,
        predicted_direction=PredictedDirection.UP,
        base_confidence=Decimal("0.750"),
        news_items=news,
        cutoff_dt_vn=cutoff,
    )
    assert overlay.high_volatility_flag is True
    assert 10.0 <= overlay.band_widening_pct <= 25.0
    assert overlay.lower_bound_vnd < 19_500
    assert overlay.upper_bound_vnd > 20_500
    assert overlay.confidence_score < Decimal("0.750")
    assert len(overlay.top_news_payload) == 3

    p1 = build_backtest_point(
        forecast_date=date(2026, 11, 10),
        target_date=date(2026, 11, 11),
        actual_date=date(2026, 11, 11),
        baseline_price_vnd=100_000_000,
        predicted_price_vnd=101_000_000,
        lower_bound_vnd=99_500_000,
        upper_bound_vnd=102_000_000,
        actual_price_vnd=101_500_000,
        domain=CommodityCategory.GOLD,
        horizon=ForecastHorizon.T_PLUS_1,
        commodity_code="GOLD_SJC_BAR_SJC",
        residual_value=0.0049,
    )
    p2 = build_backtest_point(
        forecast_date=date(2026, 11, 12),
        target_date=date(2026, 11, 13),
        actual_date=date(2026, 11, 13),
        baseline_price_vnd=100_000_000,
        predicted_price_vnd=99_000_000,
        lower_bound_vnd=98_000_000,
        upper_bound_vnd=100_000_000,
        actual_price_vnd=101_000_000,
        domain=CommodityCategory.GOLD,
        horizon=ForecastHorizon.T_PLUS_1,
        commodity_code="GOLD_SJC_BAR_SJC",
        residual_value=0.020,
    )
    wf = summarize_walk_forward([p1, p2])
    assert wf.sample_count == 2
    assert wf.mae_vnd == 1_250_000
    assert wf.naive_mae_vnd == 1_250_000
    assert wf.directional_accuracy_pct == 50.0
    assert wf.interval_coverage_pct == 50.0

    # Confirm ML packages remained strictly isolated out of API/shared pyproject.toml
    assert "numpy" not in _API_TOML.read_text(encoding="utf-8").lower()
    assert "numpy" not in _SHARED_TOML.read_text(encoding="utf-8").lower()


def test_fuel_mid_cycle_zero_lookahead_and_direction() -> None:
    # Last fuel adjustment was 3 days ago; mid-cycle Brent uptick <= as_of_date drives direction,
    # while a +500% shock on as_of_date + 1 must have zero effect (strict Zero Look-Ahead Bias).
    as_of = date(2026, 11, 22)
    as_of_dt = datetime(2026, 11, 22, 12, 0, tzinfo=VN_TZ)
    last_adj = date(2026, 11, 19)

    _, f_base, _, m_base, n_base = _build_synthetic_inputs(last_adj, fuel_cycles=20, include_future_shock=False)
    for d_off in range(1, 4):
        d = last_adj + timedelta(days=d_off)
        m_base.by_code[MacroIndicatorCode.BRENT_USD.value][d] = Decimal("86.50") + Decimal(d_off)
        m_base.by_code[MacroIndicatorCode.WTI_USD.value][d] = Decimal("82.50") + Decimal(d_off)
        m_base.by_code[MacroIndicatorCode.USD_VND_VCB.value][d] = Decimal("26150")
        m_base.by_code[MacroIndicatorCode.USD_VND_MARKET.value][d] = Decimal("25900")

    m_shock = MacroSeriesBundle(
        by_code={code: dict(series) for code, series in m_base.by_code.items()}
    )
    f_shock = list(f_base)
    for fut in range(1, 6):
        fd = as_of + timedelta(days=fut)
        f_shock.append(PriceObs(fd, 99_000, True, SeriesOrigin.LIVE_ROLLUP.value, "FUTURE_SHOCK"))
        m_shock.by_code[MacroIndicatorCode.BRENT_USD.value][fd] = Decimal("250.0")
        m_shock.by_code[MacroIndicatorCode.WTI_USD.value][fd] = Decimal("240.0")
        m_shock.by_code[MacroIndicatorCode.USD_VND_VCB.value][fd] = Decimal("34000")

    out_base = generate_fuel_forecast(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        as_of_dt_vn=as_of_dt,
        price_series=f_base,
        macro_bundle=m_base,
        news_items=n_base,
    )
    out_shock = generate_fuel_forecast(
        commodity_code="FUEL_RON95_III",
        location_code="VUNG_1",
        as_of_dt_vn=as_of_dt,
        price_series=f_shock,
        macro_bundle=m_shock,
        news_items=n_base,
    )

    assert out_base.status == ForecastStatus.READY
    assert out_base.model_version == FUEL_MODEL_VERSION
    assert out_base.point_estimate_vnd == out_shock.point_estimate_vnd
    assert out_base.lower_bound_vnd == out_shock.lower_bound_vnd
    assert out_base.upper_bound_vnd == out_shock.upper_bound_vnd
    assert out_base.drivers_json == out_shock.drivers_json


@pytest.mark.asyncio
async def test_dispatch_github_workflow_forecast_task_type() -> None:
    settings = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": "vxg_prod_9f8e7d6c5b4a3210fedcba987654",
            "WORKER_DISPATCH_MODE": "github_actions",
            "GITHUB_REPO": "acme/vxg-tracker",
            "GITHUB_WORKFLOW_FILE": "crawl-schedule.yml",
            "GITHUB_WORKFLOW_REF": "main",
            "GITHUB_DISPATCH_TOKEN": "ghp_mock_dispatch_secret",
        }
    )
    run_id = uuid.uuid4()
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await dispatch_github_workflow(
            settings=settings,
            run_id=run_id,
            scope=CrawlScope.ALL,
            task_type="forecast",
            http_client=client,
        )

    assert seen == [
        {
            "ref": "main",
            "inputs": {"scope": "ALL", "run_id": str(run_id), "task_type": "forecast"},
        }
    ]

