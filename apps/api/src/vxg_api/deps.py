"""Shared FastAPI dependencies for database sessions and admin API key verification."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator

from fastapi import Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vxg_api.config import ApiSettings
from vxg_api.errors import AppError

ADMIN_API_KEY_HEADER = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to the FastAPI application's session factory."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def require_admin_api_key(
    request: Request,
    x_admin_api_key: str | None = Security(ADMIN_API_KEY_HEADER),
) -> None:
    """Validate X-Admin-API-Key header using constant-time comparison."""
    settings: ApiSettings = request.app.state.settings
    if settings.is_production and not settings.is_admin_key_strong():
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="ADMIN_API_DISABLED_INSECURE_KEY",
            message="Admin endpoint disabled due to insecure production key configuration.",
        )

    if x_admin_api_key is None or not x_admin_api_key.strip():
        raise AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="ADMIN_API_KEY_MISSING",
            message="Missing X-Admin-API-Key header.",
        )

    configured_key: str = settings.admin_api_key
    if not configured_key or not hmac.compare_digest(
        x_admin_api_key.encode("utf-8"),
        configured_key.encode("utf-8"),
    ):
        raise AppError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="ADMIN_API_KEY_INVALID",
            message="Invalid X-Admin-API-Key.",
        )

