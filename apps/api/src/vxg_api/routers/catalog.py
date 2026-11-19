"""Catalog router exposing GET /api/v1/catalog."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_api.deps import get_db_session
from vxg_api.schemas.catalog import CatalogResponse
from vxg_api.services.catalog_query import get_catalog

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get("/catalog", response_model=CatalogResponse)
async def read_catalog(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CatalogResponse:
    """Return tracked cities, domains, commodities, and active sources."""
    return await get_catalog(session)

