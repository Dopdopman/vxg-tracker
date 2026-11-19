"""Shared Pydantic v2 types and Vietnam timezone serializers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

from vxg_api.errors import ErrorResponse
from vxg_shared.timezone import to_vn_tz


def to_vn_iso(dt: datetime) -> str:
    """Convert timezone-aware datetime to ISO-8601 string in Asia/Ho_Chi_Minh (+07:00)."""
    return to_vn_tz(dt).isoformat()


def _validate_vn_datetime(dt: datetime) -> datetime:
    return to_vn_tz(dt)


def _serialize_vn_datetime(dt: datetime) -> str:
    return to_vn_tz(dt).isoformat()


VnDatetime = Annotated[
    datetime,
    AfterValidator(_validate_vn_datetime),
    PlainSerializer(_serialize_vn_datetime, return_type=str, when_used="json"),
]


class CityCode(StrEnum):
    """Supported Vietnamese cities in Phase 1."""

    HN = "HN"
    DN = "DN"
    HCM = "HCM"


__all__ = ["CityCode", "ErrorResponse", "VnDatetime", "to_vn_iso"]

