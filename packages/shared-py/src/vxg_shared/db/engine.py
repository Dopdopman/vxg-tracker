"""Async SQLAlchemy engine and session factory helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vxg_shared.settings import SharedSettings


def create_async_db_engine(database_url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """Create an async PostgreSQL engine with health pre-ping enabled."""
    resolved_url = database_url or SharedSettings().async_database_url
    return create_async_engine(
        resolved_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory safe for concurrent async tasks."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

