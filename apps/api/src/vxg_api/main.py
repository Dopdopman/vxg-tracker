"""FastAPI entrypoint for vxg-tracker API Server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vxg_api.config import ApiSettings
from vxg_api.errors import register_exception_handlers
from vxg_api.routers.admin import router as admin_router
from vxg_api.routers.audit import router as audit_router
from vxg_api.routers.catalog import router as catalog_router
from vxg_api.routers.health import router as health_router
from vxg_api.routers.prices import router as prices_router
from vxg_shared.db.engine import create_async_db_engine, create_session_factory


def create_app(custom_settings: ApiSettings | None = None) -> FastAPI:
    """Build and configure the FastAPI application with lifespan DB engine and routers."""
    settings = custom_settings or ApiSettings()
    settings.validate_production_security()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_db_engine(settings.async_database_url)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="vxg-tracker API",
        version="0.1.0",
        description="Verified daily Vietnamese essential prices (Gold, Fuel, Gas) & Audit API",
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Admin-API-Key"],
    )

    app.include_router(health_router)
    app.include_router(catalog_router)
    app.include_router(prices_router)
    app.include_router(audit_router)
    app.include_router(admin_router)
    return app


app = create_app()
