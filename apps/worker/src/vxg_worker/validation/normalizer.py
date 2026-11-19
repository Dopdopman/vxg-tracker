"""Currency and unit normalization helpers for Vietnamese market prices."""

from __future__ import annotations

import math
import re
from typing import Literal

_DIGIT_GROUP_RE = re.compile(r"[^\d]")


def parse_vnd_int(raw: str | int | float | None) -> int | None:
    """Parse Vietnamese formatted currency string or integer into whole VND int."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw) or not raw.is_integer():
            return None
        return int(raw)
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.startswith("-"):
        return None
    digits = _DIGIT_GROUP_RE.sub("", cleaned)
    if not digits:
        return None
    return int(digits)


def normalize_gold_price_to_luong(
    value: int,
    *,
    source_unit: Literal["LUONG", "CHI", "THOUSAND_PER_LUONG", "THOUSAND_PER_CHI"],
) -> int:
    """Normalize gold prices into whole VND per lượng (1 lượng = 10 chỉ)."""
    multipliers = {
        "LUONG": 1,
        "CHI": 10,
        "THOUSAND_PER_LUONG": 1_000,
        "THOUSAND_PER_CHI": 10_000,
    }
    return value * multipliers[source_unit]

