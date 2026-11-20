"""PostgreSQL integration tests for Milestone 9 forecasts, backtest audit & admin triggers."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from vxg_api.config import ApiSettings
from vxg_api.main import create_app
from vxg_shared.db.engine import create_async_db_engine, create_session_factory
from vxg_shared.db.models import (
    DailyPriceSeries,
    ForecastEvaluation,
    ForecastRun,
    MacroIndicator,
    PriceForecast,
)
from vxg_shared.enums import (
    CrawlRunStatus,
    CrawlScope,
    ForecastEvaluationType,
    ForecastHorizon,
    ForecastStatus,
    MacroIndicatorCode,
    PredictedDirection,
    SeriesOrigin,
    TriggerType,
)
from vxg_shared.timezone import vn_today
from vxg_worker.forecasting.evaluator import evaluate_due_forecasts
from vxg_worker.forecasting.pipeline import run_forecast_pipeline
from vxg_worker.persistence import load_catalog_lookup

_HOST_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker",
)
_ADMIN_KEY = "test-milestone9-admin-key-secret"


def _make_api_settings(*, dispatch_mode: str = "db_poll") -> ApiSettings:
    return ApiSettings.model_validate(
        {
            "DATABASE_URL": _HOST_TEST_DB_URL,
            "DATABASE_URL_SYNC": None,
            "CORS_ORIGINS": "http://localhost:3000",
            "ADMIN_API_KEY": _ADMIN_KEY,
            "WORKER_DISPATCH_MODE": dispatch_mode,
            "GITHUB_REPO": "acme/vxg-tracker",
            "GITHUB_DISPATCH_TOKEN": "ghp_mock_dispatch_secret",
        }
    )


def _snapshot_forecast_row(r: PriceForecast) -> dict[str, Any]:
    return {
        "id": r.id,
        "commodity_id": r.commodity_id,
        "location_id": r.location_id,
        "forecast_date": r.forecast_date,
        "target_date": r.target_date,
        "horizon_type": r.horizon_type,
        "model_version": r.model_version,
        "status": r.status,
        "baseline_price_vnd": r.baseline_price_vnd,
        "point_estimate_vnd": r.point_estimate_vnd,
        "lower_bound_vnd": r.lower_bound_vnd,
        "upper_bound_vnd": r.upper_bound_vnd,
        "expected_change_vnd": r.expected_change_vnd,
        "expected_change_pct": r.expected_change_pct,
        "predicted_direction": r.predicted_direction,
        "confidence_score": r.confidence_score,
        "news_adjustment_pct": r.news_adjustment_pct,
        "insufficient_reason": r.insufficient_reason,
        "drivers_json": dict(r.drivers_json or {}),
        "created_at": r.created_at,
    }


def _snapshot_eval_row(r: ForecastEvaluation) -> dict[str, Any]:
    return {
        "id": r.id,
        "forecast_id": r.forecast_id,
        "commodity_id": r.commodity_id,
        "location_id": r.location_id,
        "horizon_type": r.horizon_type,
        "model_version": r.model_version,
        "evaluation_type": r.evaluation_type,
        "forecast_date": r.forecast_date,
        "target_date": r.target_date,
        "actual_date": r.actual_date,
        "baseline_price_vnd": r.baseline_price_vnd,
        "predicted_price_vnd": r.predicted_price_vnd,
        "lower_bound_vnd": r.lower_bound_vnd,
        "upper_bound_vnd": r.upper_bound_vnd,
        "actual_price_vnd": r.actual_price_vnd,
        "abs_error_vnd": r.abs_error_vnd,
        "pct_error": r.pct_error,
        "predicted_direction": r.predicted_direction,
        "actual_direction": r.actual_direction,
        "direction_correct": r.direction_correct,
        "within_interval": r.within_interval,
        "evaluated_at": r.evaluated_at,
    }


@pytest.mark.asyncio
async def test_forecast_pipeline_evaluations_separation_and_all_apis() -> None:
    engine = create_async_db_engine(_HOST_TEST_DB_URL)
    session_factory = create_session_factory(engine)
    today = vn_today()
    due_forecast_date = today - timedelta(days=5)
    due_target_date = today - timedelta(days=1)

    pnj_dates = [today - timedelta(days=delta) for delta in range(85, -1, -1)]
    created_run_ids: list[uuid.UUID] = []
    due_forecast_id: uuid.UUID | None = None

    backup_daily: list[dict[str, Any]] = []
    backup_macro: list[dict[str, Any]] = []
    backup_forecasts: list[dict[str, Any]] = []
    backup_evals: list[dict[str, Any]] = []
    pnj_id = 0
    hcm_id = 0
    gold_ids: list[int] = []

    try:
        async with session_factory() as session:
            # Verify Migration 0004 check constraint blocks LIVE evaluation without forecast_id
            catalog = await load_catalog_lookup(session)
            pnj = catalog.commodities_by_code["GOLD_SJC_BAR_PNJ"]
            hcm = catalog.locations_by_code["HCM"]
            pnj_id = int(pnj.id)
            hcm_id = int(hcm.id)
            gold_ids = [
                int(c.id) for c in catalog.commodities_by_code.values() if c.category == "GOLD"
            ]

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "INSERT INTO forecast_evaluations "
                            "(forecast_id, commodity_id, location_id, horizon_type, model_version, "
                            "evaluation_type, forecast_date, target_date, actual_date, "
                            "baseline_price_vnd, predicted_price_vnd, actual_price_vnd, "
                            "abs_error_vnd, pct_error, predicted_direction, actual_direction, "
                            "direction_correct, within_interval) "
                            "VALUES (NULL, :cid, :lid, 'T_PLUS_1', 'test_v1', 'LIVE', "
                            ":d1, :d2, :d2, 100000000, 101000000, 101000000, 0, 0, 'UP', 'UP', true, true)"
                        ),
                        {"cid": pnj_id, "lid": hcm_id, "d1": due_forecast_date, "d2": due_target_date},
                    )

            # Backup pre-existing rows touched on PNJ/HCM and today's GOLD forecasts/evals
            old_daily = (
                await session.execute(
                    select(DailyPriceSeries).where(
                        DailyPriceSeries.commodity_id == pnj_id,
                        DailyPriceSeries.location_id == hcm_id,
                        DailyPriceSeries.price_date.in_(pnj_dates),
                    )
                )
            ).scalars().all()
            for r in old_daily:
                backup_daily.append(
                    {
                        "id": r.id,
                        "commodity_id": r.commodity_id,
                        "location_id": r.location_id,
                        "price_date": r.price_date,
                        "buy_price_vnd": r.buy_price_vnd,
                        "sell_price_vnd": r.sell_price_vnd,
                        "retail_price_vnd": r.retail_price_vnd,
                        "reference_price_vnd": r.reference_price_vnd,
                        "series_origin": r.series_origin,
                        "is_adjustment_day": r.is_adjustment_day,
                        "source_record_id": r.source_record_id,
                        "source_code": r.source_code,
                        "source_url": r.source_url,
                        "evidence_sha256": r.evidence_sha256,
                        "metadata_json": dict(r.metadata_json or {}),
                        "updated_at": r.updated_at,
                    }
                )

            old_macro = (
                await session.execute(
                    select(MacroIndicator).where(MacroIndicator.indicator_date.in_(pnj_dates))
                )
            ).scalars().all()
            for m in old_macro:
                backup_macro.append(
                    {
                        "id": m.id,
                        "indicator_code": m.indicator_code,
                        "indicator_date": m.indicator_date,
                        "value": m.value,
                        "open_value": m.open_value,
                        "high_value": m.high_value,
                        "low_value": m.low_value,
                        "change_pct_1d": m.change_pct_1d,
                        "unit": m.unit,
                        "source_code": m.source_code,
                        "source_url": m.source_url,
                        "content_sha256": m.content_sha256,
                        "metadata_json": dict(m.metadata_json or {}),
                        "fetched_at": m.fetched_at,
                    }
                )

            old_fc = (
                await session.execute(
                    select(PriceForecast).where(
                        PriceForecast.forecast_date.in_([today, due_forecast_date])
                    )
                )
            ).scalars().all()
            for pf in old_fc:
                backup_forecasts.append(_snapshot_forecast_row(pf))

            old_ev = (
                await session.execute(
                    select(ForecastEvaluation).where(
                        (ForecastEvaluation.commodity_id == pnj_id)
                        | (ForecastEvaluation.forecast_date == today)
                        | (
                            (ForecastEvaluation.evaluation_type == ForecastEvaluationType.BACKTEST.value)
                            & ForecastEvaluation.commodity_id.in_(gold_ids)
                        )
                    )
                )
            ).scalars().all()
            for ev in old_ev:
                backup_evals.append(_snapshot_eval_row(ev))

            # Seed deterministic daily series for PNJ HCM + macro series so test passes in empty CI DB too
            await session.execute(
                delete(DailyPriceSeries).where(
                    DailyPriceSeries.commodity_id == pnj_id,
                    DailyPriceSeries.location_id == hcm_id,
                    DailyPriceSeries.price_date.in_(pnj_dates),
                )
            )
            daily_seed = [
                {
                    "commodity_id": pnj_id,
                    "location_id": hcm_id,
                    "price_date": d,
                    "buy_price_vnd": 127_000_000 + idx * 90_000 + ((idx % 5) * 150_000),
                    "sell_price_vnd": 130_000_000 + idx * 90_000 + ((idx % 5) * 150_000),
                    "retail_price_vnd": None,
                    "reference_price_vnd": 130_000_000 + idx * 90_000 + ((idx % 5) * 150_000),
                    "series_origin": SeriesOrigin.BACKFILL.value,
                    "is_adjustment_day": True,
                    "source_code": "M9TEST_SERIES",
                    "source_url": "https://example.test/pnj",
                }
                for idx, d in enumerate(pnj_dates)
            ]
            await session.execute(pg_insert(DailyPriceSeries).values(daily_seed))

            macro_seed: list[dict[str, Any]] = []
            for idx, d in enumerate(pnj_dates):
                for code, unit, val in (
                    (MacroIndicatorCode.XAU_USD.value, "USD_PER_OZ", Decimal(str(3500 + idx * 1.5))),
                    (MacroIndicatorCode.DXY.value, "INDEX_POINTS", Decimal(str(104.0 - (idx % 6) * 0.2))),
                    (MacroIndicatorCode.BRENT_USD.value, "USD_PER_BBL", Decimal(str(88.0 + (idx % 8) * 0.4))),
                    (MacroIndicatorCode.WTI_USD.value, "USD_PER_BBL", Decimal(str(83.0 + (idx % 8) * 0.3))),
                    (MacroIndicatorCode.USD_VND_VCB.value, "VND_PER_USD", Decimal("25960.0000")),
                ):
                    macro_seed.append(
                        {
                            "indicator_code": code,
                            "indicator_date": d,
                            "value": val,
                            "unit": unit,
                            "source_code": "M9TEST_MACRO",
                            "source_url": "https://example.test/macro",
                        }
                    )
            await session.execute(
                pg_insert(MacroIndicator).values(macro_seed).on_conflict_do_nothing()
            )
            await session.commit()

        # Run GOLD pipeline twice with backtests persisted -> check idempotency and counts
        sum1 = await run_forecast_pipeline(
            engine,
            session_factory,
            scope=CrawlScope.GOLD,
            as_of_date=today,
            persist_backtest=True,
            run_evaluation_first=False,
            advisory_lock_key=908_712_931,
        )
        sum2 = await run_forecast_pipeline(
            engine,
            session_factory,
            scope=CrawlScope.GOLD,
            as_of_date=today,
            persist_backtest=True,
            run_evaluation_first=False,
            advisory_lock_key=908_712_931,
        )
        assert sum1.ready_count >= 2
        assert sum1.insufficient_count >= 1
        assert sum1.ready_count == sum2.ready_count
        assert sum1.backtest_rows_upserted > 0

        # Insert 1 due historical PriceForecast snapshot for PNJ HCM and verify LIVE evaluation
        async with session_factory() as session:
            due_pf = PriceForecast(
                commodity_id=pnj_id,
                location_id=hcm_id,
                forecast_date=due_forecast_date,
                target_date=due_target_date,
                horizon_type=ForecastHorizon.T_PLUS_1.value,
                model_version="m9_live_test_v1",
                status=ForecastStatus.READY.value,
                baseline_price_vnd=135_000_000,
                point_estimate_vnd=137_000_000,
                lower_bound_vnd=134_000_000,
                upper_bound_vnd=140_000_000,
                expected_change_vnd=2_000_000,
                expected_change_pct=Decimal("1.481"),
                predicted_direction=PredictedDirection.UP.value,
                confidence_score=Decimal("0.680"),
                news_adjustment_pct=Decimal("0.000"),
                drivers_json={"backtest": {"sample_count": 5, "mae_vnd": 100_000, "mape_pct": 0.1}},
            )
            session.add(due_pf)
            await session.flush()
            due_forecast_id = due_pf.id
            await session.commit()

            eval_res1 = await evaluate_due_forecasts(session, as_of_date=today)
            await session.commit()
            eval_res2 = await evaluate_due_forecasts(session, as_of_date=today)
            await session.commit()

            assert eval_res1.evaluated_inserted >= 1
            assert eval_res2.evaluated_inserted == 0

            ev_live = (
                await session.execute(
                    select(ForecastEvaluation).where(ForecastEvaluation.forecast_id == due_forecast_id)
                )
            ).scalar_one()
            assert ev_live.evaluation_type == ForecastEvaluationType.LIVE.value

        # Verify all 4 FastAPI endpoints cleanly (including auth checks & conflict handling)
        app = create_app(_make_api_settings(dispatch_mode="db_poll"))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            latest_resp = await client.get("/api/v1/forecasts/latest?domain=GOLD&location=HCM")
            assert latest_resp.status_code == 200
            latest_json = latest_resp.json()
            assert latest_json["count"] >= 2
            statuses = {i["status"] for i in latest_json["items"]}
            assert "READY" in statuses and "INSUFFICIENT_DATA" in statuses

            detail_resp = await client.get(
                "/api/v1/forecasts/detail?commodity_code=GOLD_SJC_BAR_PNJ&location=HCM&horizon=T_PLUS_1&history_range=30d"
            )
            assert detail_resp.status_code == 200
            detail_json = detail_resp.json()
            assert detail_json["forecast"]["status"] == "READY"
            assert len(detail_json["history_points"]) >= 25
            assert len(detail_json["macro_highlights"]) == 3
            assert detail_json["backtest_summary"]["sample_count"] > 0

            bad_zone = await client.get(
                "/api/v1/forecasts/detail?commodity_code=GOLD_SJC_BAR_PNJ&location=VUNG_1"
            )
            assert bad_zone.status_code == 422
            assert bad_zone.json()["code"] == "INVALID_LOCATION_FOR_DOMAIN"

            audit_all = await client.get("/api/v1/audit/forecasts?evaluation_type=ALL&limit=10")
            assert audit_all.status_code == 200
            audit_data = audit_all.json()
            assert audit_data["live_summary"]["evaluation_type"] == "LIVE"
            assert audit_data["live_summary"]["total_evaluated"] >= 1
            assert audit_data["backtest_summary"]["evaluation_type"] == "BACKTEST"
            assert audit_data["backtest_summary"]["total_evaluated"] >= 1

            audit_live = await client.get("/api/v1/audit/forecasts?evaluation_type=LIVE&limit=20")
            assert audit_live.status_code == 200
            assert all(item["evaluation_type"] == "LIVE" for item in audit_live.json()["items"])

            # Admin trigger endpoint auth + 202 + 409 conflict
            assert (await client.post("/api/v1/admin/forecast-runs")).status_code == 401
            assert (
                await client.post(
                    "/api/v1/admin/forecast-runs",
                    headers={"X-Admin-API-Key": "wrong-key"},
                )
            ).status_code == 403

            queued_resp = await client.post(
                "/api/v1/admin/forecast-runs",
                headers={"X-Admin-API-Key": _ADMIN_KEY},
                json={"scope": "GOLD"},
            )
            assert queued_resp.status_code == 202
            run_id = uuid.UUID(queued_resp.json()["run_id"])
            created_run_ids.append(run_id)
            assert queued_resp.json()["status"] == CrawlRunStatus.QUEUED.value

            conflict_resp = await client.post(
                "/api/v1/admin/forecast-runs",
                headers={"X-Admin-API-Key": _ADMIN_KEY},
                json={"scope": "ALL"},
            )
            assert conflict_resp.status_code == 409
            assert conflict_resp.json()["code"] == "FORECAST_RUN_ALREADY_ACTIVE"

        # Verify 502 error handling when GitHub Actions workflow_dispatch fails
        app_gh = create_app(_make_api_settings(dispatch_mode="github_actions"))
        async with (
            app_gh.router.lifespan_context(app_gh),
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _r: httpx.Response(500, text="gh down"))
            ) as mock_gh,
        ):
            app_gh.state.dispatch_http_client = mock_gh
            # Clear active GOLD run so we can dispatch cleanly
            async with session_factory() as s_clr:
                await s_clr.execute(delete(ForecastRun).where(ForecastRun.id.in_(created_run_ids)))
                await s_clr.commit()
                created_run_ids.clear()

            async with httpx.AsyncClient(
                transport=ASGITransport(app=app_gh), base_url="http://testserver"
            ) as gh_client:
                gh_resp = await gh_client.post(
                    "/api/v1/admin/forecast-runs",
                    headers={"X-Admin-API-Key": _ADMIN_KEY},
                    json={"scope": "FUEL"},
                )
                assert gh_resp.status_code == 502
                assert gh_resp.json()["code"] == "GITHUB_DISPATCH_FAILED"
                created_run_ids.append(uuid.UUID(gh_resp.json()["details"]["run_id"]))

                async with session_factory() as s_chk:
                    failed_run = (
                        await s_chk.execute(
                            select(ForecastRun).where(ForecastRun.id == created_run_ids[-1])
                        )
                    ).scalar_one()
                    assert failed_run.status == CrawlRunStatus.FAILED.value
                    assert failed_run.trigger_type == TriggerType.MANUAL_ADMIN.value
    finally:
        async with session_factory() as cleanup:
            if created_run_ids:
                await cleanup.execute(
                    delete(ForecastRun).where(ForecastRun.id.in_(created_run_ids))
                )
            if due_forecast_id is not None:
                await cleanup.execute(
                    delete(ForecastEvaluation).where(ForecastEvaluation.forecast_id == due_forecast_id)
                )
                await cleanup.execute(
                    delete(PriceForecast).where(PriceForecast.id == due_forecast_id)
                )

            if gold_ids:
                await cleanup.execute(
                    delete(ForecastEvaluation).where(
                        (ForecastEvaluation.commodity_id == pnj_id)
                        | (ForecastEvaluation.forecast_date == today)
                        | (
                            (ForecastEvaluation.evaluation_type == ForecastEvaluationType.BACKTEST.value)
                            & ForecastEvaluation.commodity_id.in_(gold_ids)
                        )
                    )
                )
            await cleanup.execute(
                delete(PriceForecast).where(
                    PriceForecast.forecast_date.in_([today, due_forecast_date])
                )
            )
            if backup_forecasts:
                await cleanup.execute(pg_insert(PriceForecast).values(backup_forecasts))
            if backup_evals:
                await cleanup.execute(pg_insert(ForecastEvaluation).values(backup_evals))

            if pnj_id and hcm_id:
                await cleanup.execute(
                    delete(DailyPriceSeries).where(
                        DailyPriceSeries.commodity_id == pnj_id,
                        DailyPriceSeries.location_id == hcm_id,
                        DailyPriceSeries.price_date.in_(pnj_dates),
                    )
                )
            if backup_daily:
                await cleanup.execute(pg_insert(DailyPriceSeries).values(backup_daily))

            await cleanup.execute(
                delete(MacroIndicator).where(MacroIndicator.indicator_date.in_(pnj_dates))
            )
            if backup_macro:
                await cleanup.execute(pg_insert(MacroIndicator).values(backup_macro))
            await cleanup.commit()
        await engine.dispose()
