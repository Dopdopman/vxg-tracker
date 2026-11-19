"""Async and sync SQLAlchemy engine helpers with Neon auto-suspend resilience."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vxg_shared.settings import (
    SharedSettings,
    normalize_async_database_url,
    normalize_sync_database_url,
)


def create_async_db_engine(database_url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """Create an async PostgreSQL engine with pre-ping and recycle for Neon suspend wakeup."""
    resolved_url = (
        normalize_async_database_url(database_url)
        if database_url is not None
        else SharedSettings().async_database_url
    )
    return create_async_engine(
        resolved_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
    )


def create_sync_db_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a sync psycopg PostgreSQL engine with pre-ping and recycle for migrations."""
    resolved_url = (
        normalize_sync_database_url(database_url)
        if database_url is not None
        else SharedSettings().sync_database_url
    )
    return create_engine(
        resolved_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory safe for concurrent async tasks."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
