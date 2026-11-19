"""Configuration for FastAPI server extending SharedSettings."""

from __future__ import annotations

from pydantic import AliasChoices, Field

from vxg_shared.settings import SharedSettings

MIN_PRODUCTION_ADMIN_KEY_LENGTH = 24
WEAK_ADMIN_API_KEYS: frozenset[str] = frozenset(
    {
        "",
        "change-me-local-admin-key",
        "changeme",
        "admin",
        "secret",
        "test",
    }
)


class ApiSettings(SharedSettings):
    """Runtime settings for the vxg-api server."""

    api_port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "API_PORT"))
    cors_origins_raw: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("CORS_ORIGINS", "API_CORS_ORIGINS"),
    )
    admin_api_key: str = Field(default="", validation_alias="ADMIN_API_KEY")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    def is_admin_key_strong(self) -> bool:
        """Return True if ADMIN_API_KEY satisfies minimum production security requirements."""
        key = self.admin_api_key.strip()
        if len(key) < MIN_PRODUCTION_ADMIN_KEY_LENGTH:
            return False
        lowered = key.lower()
        return lowered not in WEAK_ADMIN_API_KEYS and not lowered.startswith("change-me")

    def validate_production_security(self) -> None:
        """Fail-fast in production when ADMIN_API_KEY is missing, default, or too short."""
        if self.is_production and not self.is_admin_key_strong():
            raise RuntimeError(
                "Refusing to start vxg-api in production with a missing, default, or weak "
                f"ADMIN_API_KEY (minimum {MIN_PRODUCTION_ADMIN_KEY_LENGTH} characters required)."
            )
