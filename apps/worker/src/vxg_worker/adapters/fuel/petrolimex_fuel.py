"""Official Petrolimex retail fuel price adapter (petrolimex.com.vn)."""

from __future__ import annotations

import re

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import normalize_space, parse_html, parse_vn_datetime
from vxg_worker.adapters.fuel.zone_table import parse_fuel_zone_table
from vxg_worker.fetcher.types import FetchResult

_PLX_TIME_RE = re.compile(r"cập nhật lúc\s*(\d{1,2}:\d{2}\s*-\s*\d{1,2}/\d{1,2}/\d{4})", re.I)


class PetrolimexFuelAdapter(BaseSourceAdapter):
    """Parses Petrolimex's official Vùng 1 & Vùng 2 fuel price table."""

    parser_key = "fuel.petrolimex_fuel"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        body_text = normalize_space(tree.body.text(strip=True)) if tree.body else ""
        published_vn = None
        if match := _PLX_TIME_RE.search(body_text):
            published_vn = parse_vn_datetime(match.group(1))

        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        candidates = parse_fuel_zone_table(
            tree,
            effective_date=effective_date,
            source_published_at=published_vn,
            crawled_at=crawled_vn,
            source_url=proof_url,
            evidence_sha256=sha256,
        )

        if len(candidates) < 6:
            raise SchemaDriftError(
                f"Petrolimex fuel canary failed: expected >= 6 zone entries, parsed {len(candidates)}"
            )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "effective_date": effective_date.isoformat(),
                "entries_parsed": len(candidates),
            },
        )

