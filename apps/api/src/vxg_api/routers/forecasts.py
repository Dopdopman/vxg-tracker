"""Public read-only forecast snapshots and detail endpoints (no ML runtime libraries)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.deps import get_db_session
from vxg_api.errors import ErrorResponse
from vxg_api.schemas.common import CityCode
from vxg_api.schemas.forecasts import ForecastDetailResponse, LatestForecastsResponse
from vxg_api.schemas.prices import HistoryLocationCode, HistoryRange
from vxg_api.services.forecast_query import (
    get_forecast_detail,
    get_latest_forecasts,
    parse_forecast_horizon,
)
from vxg_shared.enums import CommodityCategory

router = APIRouter(prefix="/api/v1/forecasts", tags=["forecasts"])


@router.get(
    "/latest",
    response_model=LatestForecastsResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_latest_forecasts(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    domain: Annotated[
        CommodityCategory | None,
        Query(description="Optional domain filter (GOLD, FUEL, GAS)."),
    ] = None,
    location: Annotated[
        CityCode,
        Query(description="City code (HN, DN, HCM); auto-mapped to fuel zone for FUEL."),
    ] = CityCode.HCM,
    horizon: Annotated[
        str | None,
        Query(
            description=(
                "Optional horizon filter (T_PLUS_1, T_PLUS_7, NEXT_FUEL_CYCLE, NEXT_GAS_MONTH)."
            )
        ),
    ] = None,
) -> LatestForecastsResponse:
    """Return latest stored forecast snapshots including READY and INSUFFICIENT_DATA items."""
    parsed_horizon = parse_forecast_horizon(horizon)
    return await get_latest_forecasts(
        session,
        domain=domain,
        location=location,
        horizon=parsed_horizon,
    )


@router.get(
    "/detail",
    response_model=ForecastDetailResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def read_forecast_detail(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    commodity_code: Annotated[
        str,
        Query(min_length=2, max_length=64, description="Canonical commodity code (e.g. GOLD_SJC_BAR_SJC)."),
    ],
    location: Annotated[
        HistoryLocationCode,
        Query(description="City code (HN, DN, HCM) or FUEL zone (VUNG_1, VUNG_2)."),
    ] = HistoryLocationCode.HCM,
    horizon: Annotated[
        str | None,
        Query(description="Optional horizon override (defaults per commodity domain)."),
    ] = None,
    history_range: Annotated[
        HistoryRange,
        Query(description="Historical chart window: 30d, 90d, 1y, or 2y."),
    ] = HistoryRange.D90,
) -> ForecastDetailResponse:
    """Return complete /forecast view payload: snapshot, history series, macro & news."""
    parsed_horizon = parse_forecast_horizon(horizon)
    return await get_forecast_detail(
        session,
        commodity_code=commodity_code,
        location=location,
        horizon=parsed_horizon,
        history_range=history_range,
    )

