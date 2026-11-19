"""PostgreSQL session-level Advisory Lock preventing concurrent crawl runs."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class CrawlLockBusyError(RuntimeError):
    """Raised when another worker already holds the global crawl advisory lock."""


class PostgresAdvisoryLock:
    """Dedicated autocommit connection holding pg_try_advisory_lock across a crawl run."""

    def __init__(self, engine: AsyncEngine, lock_key: int) -> None:
        self._engine = engine
        self._lock_key = lock_key
        self._conn: AsyncConnection | None = None
        self.acquired: bool = False

    async def __aenter__(self) -> bool:
        raw_conn = await self._engine.connect()
        self._conn = await raw_conn.execution_options(isolation_level="AUTOCOMMIT")
        res = await self._conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": self._lock_key},
        )
        self.acquired = bool(res.scalar_one())
        if not self.acquired:
            await self._conn.close()
            self._conn = None
        return self.acquired

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is None:
            return
        try:
            if self.acquired:
                await self._conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": self._lock_key},
                )
        finally:
            await self._conn.close()
            self._conn = None
            self.acquired = False

