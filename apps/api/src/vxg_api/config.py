"""Configuration for FastAPI server extending SharedSettings."""

from __future__ import annotations

from enum import StrEnum

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


class WorkerDispatchMode(StrEnum):
    """How manual admin crawl runs are handed off to the worker runtime."""

    DB_POLL = "db_poll"
    GITHUB_ACTIONS = "github_actions"


class ApiSettings(SharedSettings):
    """Runtime settings for the vxg-api server."""

    api_port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "API_PORT"))
    cors_origins_raw: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("CORS_ORIGINS", "API_CORS_ORIGINS"),
    )
    admin_api_key: str = Field(default="", validation_alias="ADMIN_API_KEY")

    worker_dispatch_mode_raw: str = Field(
        default=WorkerDispatchMode.DB_POLL.value,
        validation_alias="WORKER_DISPATCH_MODE",
    )
    github_repo: str = Field(default="", validation_alias="GITHUB_REPO")
    github_workflow_file: str = Field(
        default="crawl-schedule.yml",
        validation_alias="GITHUB_WORKFLOW_FILE",
    )
    github_workflow_ref: str = Field(default="main", validation_alias="GITHUB_WORKFLOW_REF")
    github_dispatch_token: str = Field(default="", validation_alias="GITHUB_DISPATCH_TOKEN")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def dispatch_mode(self) -> WorkerDispatchMode:
        raw = self.worker_dispatch_mode_raw.strip().lower()
        try:
            return WorkerDispatchMode(raw)
        except ValueError as exc:
            raise ValueError(
                f"Invalid WORKER_DISPATCH_MODE '{self.worker_dispatch_mode_raw}'. "
                f"Expected one of: {', '.join(m.value for m in WorkerDispatchMode)}."
            ) from exc

    def is_admin_key_strong(self) -> bool:
        """Return True if ADMIN_API_KEY satisfies minimum production security requirements."""
        key = self.admin_api_key.strip()
        if len(key) < MIN_PRODUCTION_ADMIN_KEY_LENGTH:
            return False
        lowered = key.lower()
        return lowered not in WEAK_ADMIN_API_KEYS and not lowered.startswith("change-me")

    def validate_production_security(self) -> None:
        """Fail-fast on invalid dispatch mode, weak production admin key, or missing GitHub cfg."""
        mode = self.dispatch_mode
        if self.is_production and not self.is_admin_key_strong():
            raise RuntimeError(
                "Refusing to start vxg-api in production with a missing, default, or weak "
                f"ADMIN_API_KEY (minimum {MIN_PRODUCTION_ADMIN_KEY_LENGTH} characters required)."
            )
        if self.is_production and mode == WorkerDispatchMode.GITHUB_ACTIONS:
            repo = self.github_repo.strip()
            token = self.github_dispatch_token.strip()
            if "/" not in repo or not token:
                raise RuntimeError(
                    "Refusing to start vxg-api in production with "
                    "WORKER_DISPATCH_MODE=github_actions without valid GITHUB_REPO ('owner/repo') "
                    "and GITHUB_DISPATCH_TOKEN."
                )
