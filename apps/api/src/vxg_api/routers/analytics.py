"""Public analytics endpoints for macro indicators, gold parity spread, and market news."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.deps import get_db_session
from vxg_api.errors import AppError, ErrorResponse
from vxg_api.schemas.analytics import (
    MacroAnalyticsResponse,
    NewsAnalyticsResponse,
    NewsDomainFilter,
    NewsWindowHours,
)
from vxg_api.schemas.prices import HistoryRange
from vxg_api.services.analytics_query import get_macro_analytics, get_news_analytics
from vxg_shared.enums import MacroIndicatorCode

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _parse_indicators_csv(raw_indicators: str | None) -> list[MacroIndicatorCode] | None:
    if raw_indicators is None or not raw_indicators.strip():
        return None
    parsed: list[MacroIndicatorCode] = []
    seen: set[MacroIndicatorCode] = set()
    for token in raw_indicators.split(","):
        cleaned = token.strip().upper()
        if not cleaned:
            continue
        try:
            code = MacroIndicatorCode(cleaned)
        except ValueError as exc:
            valid_list = ", ".join(c.value for c in MacroIndicatorCode)
            raise AppError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INVALID_MACRO_INDICATOR",
                message=f"Unsupported indicator '{cleaned}'. Valid values: {valid_list}.",
            ) from exc
        if code not in seen:
            seen.add(code)
            parsed.append(code)
    return parsed or None


@router.get(
    "/macro",
    response_model=MacroAnalyticsResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_macro_analytics(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    indicators: Annotated[
        str | None,
        Query(
            description=(
                "Optional comma-separated macro codes "
                "(XAU_USD, BRENT_USD, WTI_USD, DXY, USD_VND_VCB, USD_VND_MARKET)."
            )
        ),
    ] = None,
    range: Annotated[
        HistoryRange,
        Query(description="Historical lookback window: 30d, 90d, 1y, or 2y."),
    ] = HistoryRange.D90,
) -> MacroAnalyticsResponse:
    """Return latest macro/FX readings, 1d/7d deltas, series, and SJC-vs-world gold spread."""
    parsed_codes = _parse_indicators_csv(indicators)
    return await get_macro_analytics(
        session,
        indicators=parsed_codes,
        history_range=range,
    )


@router.get(
    "/news",
    response_model=NewsAnalyticsResponse,
    responses={422: {"model": ErrorResponse}},
)
async def read_news_analytics(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    domain: Annotated[
        NewsDomainFilter,
        Query(description="Filter news signals by domain (ALL, GOLD, FUEL, GAS, MACRO)."),
    ] = NewsDomainFilter.ALL,
    hours: Annotated[
        NewsWindowHours,
        Query(description="Lookback window in hours: 24, 72, or 168."),
    ] = NewsWindowHours.H72,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum number of items returned (1..100)."),
    ] = 20,
) -> NewsAnalyticsResponse:
    """Return structured, deduplicated RSS market news signals sorted by published_at DESC."""
    return await get_news_analytics(
        session,
        domain=domain,
        hours=int(hours),
        limit=limit,
    )

