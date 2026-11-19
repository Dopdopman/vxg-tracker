"""Deterministic SHA-256 helpers for evidence snapshots and price idempotency."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from vxg_shared.timezone import to_vn_tz


def compute_content_sha256(content: bytes | str) -> str:
    """Compute lowercase SHA-256 hex digest of raw snapshot bytes or UTF-8 string."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def compute_price_idempotency_key(
    *,
    commodity_id: int,
    location_id: int,
    source_id: int,
    effective_date: date,
    source_published_at: datetime | None,
    buy_price_vnd: int | None,
    sell_price_vnd: int | None,
    retail_price_vnd: int | None,
) -> str:
    """Compute deterministic SHA-256 key to prevent duplicate price rows on re-runs.

    source_published_at is normalized to Asia/Ho_Chi_Minh at second precision so
    equivalent instants in UTC or UTC+7 always hash identically.
    """
    published_str = (
        to_vn_tz(source_published_at).replace(microsecond=0).isoformat()
        if source_published_at is not None
        else "NONE"
    )
    buy_str = str(buy_price_vnd) if buy_price_vnd is not None else "NONE"
    sell_str = str(sell_price_vnd) if sell_price_vnd is not None else "NONE"
    retail_str = str(retail_price_vnd) if retail_price_vnd is not None else "NONE"

    payload = (
        f"{commodity_id}|{location_id}|{source_id}|{effective_date.isoformat()}|"
        f"{published_str}|{buy_str}|{sell_str}|{retail_str}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

