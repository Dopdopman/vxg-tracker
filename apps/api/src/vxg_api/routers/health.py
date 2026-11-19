"""Liveness (/health) and database/catalog readiness (/ready) endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vxg_shared.db.models import Commodity, Location, Source
from vxg_shared.timezone import VN_TZ_NAME, vn_now

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe verifying process responsiveness and locked VN timezone."""
    return {
        "status": "ok",
        "service": "vxg-api",
        "timezone": VN_TZ_NAME,
        "server_time_vn": vn_now().isoformat(),
    }


@router.get("/ready")
async def readiness_check(request: Request) -> JSONResponse:
    """Readiness probe verifying PostgreSQL connectivity, schema, and seeded catalog."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            locations_count = int(
                (await session.execute(select(func.count()).select_from(Location))).scalar_one()
            )
            commodities_count = int(
                (await session.execute(select(func.count()).select_from(Commodity))).scalar_one()
            )
            sources_count = int(
                (await session.execute(select(func.count()).select_from(Source))).scalar_one()
            )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unavailable",
                "database": "error",
                "timezone": VN_TZ_NAME,
                "server_time_vn": vn_now().isoformat(),
                "detail": str(exc),
            },
        )

    catalog: dict[str, Any] = {
        "locations": locations_count,
        "commodities": commodities_count,
        "sources": sources_count,
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ready",
            "database": "connected",
            "timezone": VN_TZ_NAME,
            "server_time_vn": vn_now().isoformat(),
            "catalog": catalog,
        },
    )

