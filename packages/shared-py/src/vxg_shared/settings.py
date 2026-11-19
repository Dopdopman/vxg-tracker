"""Shared Pydantic settings with Neon/PostgreSQL URL scheme and SSL normalization."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from vxg_shared.timezone import VN_TZ_NAME

_ASYNCPG_INCOMPATIBLE_PARAMS = frozenset(
    {
        "channel_binding",
        "gssencmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "sslcrl",
    }
)
_ASYNCPG_VALID_SSL_MODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)


def normalize_async_database_url(url: str) -> str:
    """Normalize a PostgreSQL URL for asyncpg, mapping Neon sslmode & stripping channel_binding."""
    cleaned = url.strip()
    parts = urlsplit(cleaned)

    scheme = parts.scheme
    if scheme in {"postgres", "postgresql", "postgresql+psycopg"}:
        scheme = "postgresql+asyncpg"

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    if not query_items:
        return urlunsplit((scheme, parts.netloc, parts.path, "", parts.fragment))

    has_ssl = any(k == "ssl" for k, _ in query_items)
    sslmode_value: str | None = None
    filtered: list[tuple[str, str]] = []

    for key, value in query_items:
        lowered_key = key.lower()
        if lowered_key == "sslmode":
            candidate = value.strip().lower()
            if candidate in _ASYNCPG_VALID_SSL_MODES:
                sslmode_value = candidate
            continue
        if lowered_key in _ASYNCPG_INCOMPATIBLE_PARAMS:
            continue
        filtered.append((key, value))

    if not has_ssl and sslmode_value is not None:
        filtered.append(("ssl", sslmode_value))

    new_query = urlencode(filtered)
    return urlunsplit((scheme, parts.netloc, parts.path, new_query, parts.fragment))


def normalize_sync_database_url(url: str) -> str:
    """Normalize a PostgreSQL URL for synchronous psycopg/Alembic preserving SSL flags."""
    cleaned = url.strip()
    parts = urlsplit(cleaned)

    scheme = parts.scheme
    if scheme in {"postgres", "postgresql", "postgresql+asyncpg"}:
        scheme = "postgresql+psycopg"

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    if not query_items:
        return urlunsplit((scheme, parts.netloc, parts.path, "", parts.fragment))

    has_sslmode = any(k.lower() == "sslmode" for k, _ in query_items)
    normalized: list[tuple[str, str]] = []
    for key, value in query_items:
        if key.lower() == "ssl" and not has_sslmode:
            normalized.append(("sslmode", value))
        else:
            normalized.append((key, value))

    new_query = urlencode(normalized)
    return urlunsplit((scheme, parts.netloc, parts.path, new_query, parts.fragment))


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
    database_url_direct: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL_DIRECT",
    )

    @property
    def is_production(self) -> bool:
        """Return True when running under production runtime hardening."""
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def async_database_url(self) -> str:
        return normalize_async_database_url(self.database_url)

    @property
    def sync_database_url(self) -> str:
        for candidate in (self.database_url_direct, self.database_url_sync, self.database_url):
            if candidate is not None and candidate.strip():
                return normalize_sync_database_url(candidate)
        return normalize_sync_database_url(self.database_url)
