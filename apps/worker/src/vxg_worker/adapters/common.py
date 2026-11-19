"""Shared HTML table flattening, Vietnamese date parsing, and URL helpers for adapters."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from vxg_shared.timezone import VN_TZ

_SPACE_RE = re.compile(r"[\s\u00a0\ufeff]+")
_HM_DMY_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:-|lúc|\|)?\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_DMY_HMS_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?")
_VERBOSE_VN_RE = re.compile(
    r"(\d{1,2})\s*giờ\s*(\d{1,2})\s*phút\s*ngày\s*(\d{1,2})\s*(?:tháng|/)\s*(\d{1,2})\s*(?:năm|/)\s*(\d{4})",
    re.I,
)
_DMY_ONLY_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")


def normalize_space(text: str) -> str:
    """Collapse whitespace, NBSP, and BOM into single spaces."""
    return _SPACE_RE.sub(" ", text).strip()


_CITY_ALIASES: dict[str, str] = {
    "hà nội": "HN",
    "ha noi": "HN",
    "miền bắc": "HN",
    "đà nẵng": "DN",
    "da nang": "DN",
    "miền trung": "DN",
    "hồ chí minh": "HCM",
    "tp. hồ chí minh": "HCM",
    "tp hồ chí minh": "HCM",
    "tp hcm": "HCM",
    "tp.hcm": "HCM",
    "tphcm": "HCM",
}


def map_vn_city_code(label: str) -> str | None:
    """Map Vietnamese city/branch labels to canonical Phase 1 codes (HN, DN, HCM)."""
    cleaned = normalize_space(label).lower()
    return _CITY_ALIASES.get(cleaned)


def resolve_absolute_url(href: str, base_url: str) -> str:
    """Resolve relative href against base_url cleanly."""
    return urljoin(base_url, href.strip())


def parse_vn_datetime(
    text: str,
    *,
    default_hour: int = 0,
    default_minute: int = 0,
) -> datetime | None:
    """Parse common Vietnamese timestamp formats into timezone-aware Asia/Ho_Chi_Minh datetime."""
    cleaned = normalize_space(text)

    if m := _VERBOSE_VN_RE.search(cleaned):
        hour, minute, day, month, year = (int(g) for g in m.groups())
        return datetime(year, month, day, hour, minute, 0, tzinfo=VN_TZ)

    if m := _DMY_HMS_RE.search(cleaned):
        day, month, year, hour, minute = (int(g) for g in m.groups()[:5])
        second = int(m.group(6) or 0)
        return datetime(year, month, day, hour, minute, second, tzinfo=VN_TZ)

    if m := _HM_DMY_RE.search(cleaned):
        hour, minute = int(m.group(1)), int(m.group(2))
        second = int(m.group(3) or 0)
        day, month, year = int(m.group(4)), int(m.group(5)), int(m.group(6))
        return datetime(year, month, day, hour, minute, second, tzinfo=VN_TZ)

    if m := _DMY_ONLY_RE.search(cleaned):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(year, month, day, default_hour, default_minute, 0, tzinfo=VN_TZ)

    return None


def parse_html(raw_bytes: bytes) -> LexborHTMLParser:
    """Decode HTML bytes as UTF-8 with safe fallback and parse with Lexbor."""
    return LexborHTMLParser(raw_bytes.decode("utf-8", errors="replace"))


def expand_html_table(table_node: LexborNode) -> list[list[str]]:
    """Flatten an HTML table into rectangular string rows respecting rowspan and colspan."""
    grid: list[list[str]] = []
    pending_spans: dict[int, tuple[int, str]] = {}

    for tr in table_node.css("tr"):
        # Skip outer wrapper rows that contain nested tables
        if tr.css_first("table") is not None:
            continue
        row: list[str] = []
        col_idx = 0
        cells = tr.css("th, td")
        cell_pos = 0

        while cell_pos < len(cells) or col_idx in pending_spans:
            if col_idx in pending_spans:
                rem, val = pending_spans[col_idx]
                row.append(val)
                if rem > 1:
                    pending_spans[col_idx] = (rem - 1, val)
                else:
                    del pending_spans[col_idx]
                col_idx += 1
                continue

            cell = cells[cell_pos]
            cell_pos += 1
            val = normalize_space(cell.text(strip=True))
            rowspan = int(cell.attributes.get("rowspan") or "1")
            colspan = int(cell.attributes.get("colspan") or "1")

            for _ in range(max(1, colspan)):
                row.append(val)
                if rowspan > 1:
                    pending_spans[col_idx] = (rowspan - 1, val)
                col_idx += 1

        if any(row):
            grid.append(row)

    return grid
