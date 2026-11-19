"""Shared fuel product code mapping and Vùng 1 / Vùng 2 table extractor."""

from __future__ import annotations

from datetime import date, datetime

from selectolax.lexbor import LexborHTMLParser

from vxg_shared.enums import FuelZoneCode
from vxg_worker.adapters.common import expand_html_table
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import parse_vnd_int


def map_fuel_product_code(product_name: str) -> str | None:
    """Map Vietnamese fuel product labels across Petrolimex, Saigon Petro, and Webgia."""
    lower = product_name.lower()
    if "ron 95-v" in lower or "ron 95 mức 5" in lower:
        return "FUEL_RON95_V"
    if "ron 95-iii" in lower or "ron 95 mức 3" in lower:
        return "FUEL_RON95_III"
    if "e5 ron 92" in lower:
        return "FUEL_E5_RON92_II"
    if "0,001s" in lower or "0.001s" in lower:
        return "FUEL_DO_0001S_V"
    if "0,05s" in lower or "0.05s" in lower:
        return "FUEL_DO_005S_II"
    if "dầu hỏa" in lower:
        return "FUEL_KEROSENE_2K"
    return None


def parse_fuel_zone_table(
    tree: LexborHTMLParser,
    *,
    effective_date: date,
    source_published_at: datetime | None,
    crawled_at: datetime,
    source_url: str,
    evidence_sha256: str,
) -> list[PriceCandidate]:
    """Extract Vùng 1 and Vùng 2 retail fuel prices from a standard 3-column HTML table."""
    candidates: list[PriceCandidate] = []
    seen_keys: set[tuple[str, str]] = set()

    for tbl in tree.css("table"):
        rows = expand_html_table(tbl)
        if not rows:
            continue
        header_joined = " ".join(rows[0]).lower()
        if "vùng 1" not in header_joined or "vùng 2" not in header_joined:
            continue

        for row in rows[1:]:
            if len(row) < 3:
                continue
            code = map_fuel_product_code(row[0])
            if code is None:
                continue
            for loc_code, cell_val in (
                (FuelZoneCode.VUNG_1.value, parse_vnd_int(row[1])),
                (FuelZoneCode.VUNG_2.value, parse_vnd_int(row[2])),
            ):
                if cell_val is None or (code, loc_code) in seen_keys:
                    continue
                seen_keys.add((code, loc_code))
                candidates.append(
                    PriceCandidate(
                        commodity_code=code,
                        location_code=loc_code,
                        buy_price_vnd=None,
                        sell_price_vnd=None,
                        retail_price_vnd=cell_val,
                        effective_date=effective_date,
                        source_published_at=source_published_at,
                        crawled_at=crawled_at,
                        source_url=source_url,
                        evidence_sha256=evidence_sha256,
                    )
                )

    return candidates

