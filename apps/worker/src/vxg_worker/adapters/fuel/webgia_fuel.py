"""Webgia Tier 2 Cross-Check adapter for Petrolimex retail fuel prices."""

from __future__ import annotations

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import parse_html
from vxg_worker.adapters.fuel.zone_table import parse_fuel_zone_table
from vxg_worker.fetcher.types import FetchResult


class WebgiaFuelCrossCheckAdapter(BaseSourceAdapter):
    """Parses webgia.com Petrolimex fuel table for Tier 2 cross-checking."""

    parser_key = "fuel.webgia_fuel"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        crawled_vn = vn_now()
        effective_date = vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        candidates = parse_fuel_zone_table(
            tree,
            effective_date=effective_date,
            source_published_at=None,
            crawled_at=crawled_vn,
            source_url=proof_url,
            evidence_sha256=sha256,
        )

        if len(candidates) < 6:
            raise SchemaDriftError(
                f"Webgia fuel canary failed: expected >= 6 entries, parsed {len(candidates)}"
            )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "entries_parsed": len(candidates),
            },
        )

