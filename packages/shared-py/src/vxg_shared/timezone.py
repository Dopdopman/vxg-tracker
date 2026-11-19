"""Vietnam timezone helpers locked strictly to Asia/Ho_Chi_Minh (UTC+7)."""

from __future__ import annotations

import secrets
from datetime import date, datetime
from zoneinfo import ZoneInfo

VN_TZ_NAME = "Asia/Ho_Chi_Minh"
VN_TZ = ZoneInfo(VN_TZ_NAME)


def vn_now() -> datetime:
    """Return current timezone-aware datetime in Asia/Ho_Chi_Minh."""
    return datetime.now(tz=VN_TZ)


def vn_today() -> date:
    """Return current calendar date in Asia/Ho_Chi_Minh."""
    return vn_now().date()


def to_vn_tz(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to Asia/Ho_Chi_Minh.

    Rejects naive datetimes to prevent silent UTC/local drift bugs.
    """
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Naive datetime is not allowed; timezone information is required.")
    return dt.astimezone(VN_TZ)


def format_vn_run_code(
    scope: str,
    dt: datetime | None = None,
    suffix: str | None = None,
) -> str:
    """Build deterministic-format run code stamped in Asia/Ho_Chi_Minh."""
    target_dt = to_vn_tz(dt) if dt is not None else vn_now()
    token = (suffix or secrets.token_hex(2)).upper()
    return f"RUN-{target_dt:%Y%m%d-%H%M%S}-{scope.upper()}-{token}"

