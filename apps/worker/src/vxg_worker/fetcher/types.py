"""Typed fetch result and tier enums shared across fetcher and runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from vxg_shared.enums import FetchStatus


class FetcherTier(StrEnum):
    """Execution tier actually used to retrieve a source payload."""

    HTTPX = "HTTPX"
    PLAYWRIGHT = "PLAYWRIGHT"


@dataclass(slots=True)
class FetchResult:
    """Standardized output from Tier 1 (HTTPX) or Tier 2 (Playwright) fetchers."""

    request_url: str
    final_url: str | None
    http_status: int | None
    content_type: str
    raw_bytes: bytes
    content_sha256: str | None
    byte_size: int
    response_time_ms: int
    attempts: int
    tier_used: FetcherTier
    fetch_status: FetchStatus
    fallback_triggered: bool = False
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

