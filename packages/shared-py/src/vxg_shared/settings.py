"""Shared Pydantic settings with Railway/PostgreSQL URL scheme normalization."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from vxg_shared.timezone import VN_TZ_NAME


def _rewrite_db_scheme(url: str, target_driver: str) -> str:
    """Normalize PostgreSQL connection URLs for asyncpg or psycopg drivers."""
    if url.startswith("postgres://"):
        return f"postgresql+{target_driver}://{url[len('postgres://') :]}"
    if url.startswith("postgresql://"):
        return f"postgresql+{target_driver}://{url[len('postgresql://') :]}"
    if url.startswith("postgresql+psycopg://") and target_driver == "asyncpg":
        return f"postgresql+asyncpg://{url[len('postgresql+psycopg://') :]}"
    if url.startswith("postgresql+asyncpg://") and target_driver == "psycopg":
        return f"postgresql+psycopg://{url[len('postgresql+asyncpg://') :]}"
    return url


class SharedSettings(BaseSettings):
    """Base configuration shared by migrations, FastAPI server, and Worker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", validation_alias="APP_ENV")
    tz: str = Field(default=VN_TZ_NAME, validation_alias="TZ")
    database_url: str = Field(
        default="postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker",
        validation_alias="DATABASE_URL",
    )
    database_url_sync: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL_SYNC",
    )

    @property
    def is_production(self) -> bool:
        """Return True when running under production runtime hardening."""
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def async_database_url(self) -> str:
        return _rewrite_db_scheme(self.database_url, "asyncpg")

    @property
    def sync_database_url(self) -> str:
        raw = self.database_url_sync or self.database_url
        return _rewrite_db_scheme(raw, "psycopg")

