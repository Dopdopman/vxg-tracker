"""Public verified prices and evidence provenance endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.deps import get_db_session
from vxg_api.errors import ErrorResponse
from vxg_api.schemas.common import CityCode
from vxg_api.schemas.prices import LatestPricesResponse, PriceEvidenceResponse
from vxg_api.services.price_query import get_latest_prices, get_price_evidence
from vxg_shared.enums import CommodityCategory

router = APIRouter(prefix="/api/v1/prices", tags=["prices"])


@router.get(
    "/latest",
    response_model=LatestPricesResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_latest_prices(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    location: Annotated[
        CityCode | None,
        Query(description="Optional city filter (HN, DN, HCM). Defaults to all 3 cities."),
    ] = None,
    domain: Annotated[
        CommodityCategory | None,
        Query(description="Optional commodity domain filter (GOLD, FUEL, GAS)."),
    ] = None,
) -> LatestPricesResponse:
    """Return latest VERIFIED/CROSS_CHECKED prices, resolving FUEL to city fuel zones."""
    return await get_latest_prices(session, location=location, domain=domain)


@router.get(
    "/{record_id}/evidence",
    response_model=PriceEvidenceResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def read_price_evidence(
    record_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PriceEvidenceResponse:
    """Return full provenance, SHA-256 snapshot metadata, and cross-check evidence."""
    return await get_price_evidence(session, record_id)

