"""Configuration settings for the vxg-worker scraper and validation service."""

from __future__ import annotations

from pydantic import AliasChoices, Field

from vxg_shared.settings import SharedSettings


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class WorkerSettings(SharedSettings):
    """Runtime settings for Scraper Worker extending SharedSettings."""

    scraper_http_proxy: str | None = Field(default=None, validation_alias="SCRAPER_HTTP_PROXY")
    worker_poll_interval_seconds: int = Field(
        default=5, validation_alias="WORKER_POLL_INTERVAL_SECONDS"
    )
    default_http_timeout_ms: int = 15_000
    default_playwright_timeout_ms: int = 30_000
    price_spike_threshold_pct: float = Field(
        default=15.0, validation_alias="PRICE_SPIKE_THRESHOLD_PCT"
    )
    gold_max_spread_pct: float = 10.0
    advisory_lock_key: int = 908_712_001
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALERT_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALERT_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID"),
    )
    discord_webhook_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALERT_DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"),
    )
    snapshot_retention_days: int = Field(
        default=90,
        ge=1,
        validation_alias="SNAPSHOT_RETENTION_DAYS",
    )
    healthchecks_ping_url: str | None = Field(default=None, validation_alias="HEALTHCHECKS_PING_URL")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_MODEL")
    gemini_max_articles_per_run: int = Field(
        default=6,
        ge=0,
        le=20,
        validation_alias="GEMINI_MAX_ARTICLES_PER_RUN",
    )

    @property
    def cleaned_gemini_api_key(self) -> str | None:
        return _clean_optional(self.gemini_api_key)

    @property
    def proxy_url(self) -> str | None:
        return _clean_optional(self.scraper_http_proxy)

    @property
    def alert_telegram_bot_token(self) -> str | None:
        return _clean_optional(self.telegram_bot_token)

    @property
    def alert_telegram_chat_id(self) -> str | None:
        return _clean_optional(self.telegram_chat_id)

    @property
    def alert_discord_webhook_url(self) -> str | None:
        return _clean_optional(self.discord_webhook_url)
