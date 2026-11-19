"""Saigon Petro 2-step retail fuel price adapter (saigonpetro.com.vn)."""

from __future__ import annotations

from vxg_shared.db.models import Source
from vxg_shared.enums import FuelZoneCode
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import (
    expand_html_table,
    normalize_space,
    parse_html,
    parse_vn_datetime,
    resolve_absolute_url,
)
from vxg_worker.adapters.fuel.zone_table import map_fuel_product_code
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import parse_vnd_int


class SaigonPetroFuelAdapter(BaseSourceAdapter):
    """2-step adapter: locates latest fuel adjustment notice then extracts #toc-content prices."""

    parser_key = "fuel.saigonpetro_fuel"

    def extract_detail_url(self, listing_result: FetchResult, source: Source) -> str | None:
        assert_not_soft_404(listing_result, source)
        tree = parse_html(listing_result.raw_bytes)
        base = listing_result.final_url or source.base_url
        for anchor in tree.css("a"):
            href = (anchor.attributes.get("href") or "").strip()
            if "gia-ban-le-mat-hang-xang-dau-" in href:
                return resolve_absolute_url(href, base)
        return None

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("h1") or tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        toc = tree.css_first("#toc-content")
        if toc is None:
            raise SchemaDriftError("Saigon Petro fuel notice missing #toc-content container")

        toc_text = normalize_space(toc.text(strip=True))
        published_vn = parse_vn_datetime(toc_text) or parse_vn_datetime(
            doc_title, default_hour=15, default_minute=0
        )
        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        candidates: list[PriceCandidate] = []
        seen_codes: set[str] = set()

        for tbl in toc.css("table"):
            for row in expand_html_table(tbl):
                if len(row) < 2:
                    continue
                code = map_fuel_product_code(row[0])
                price_vnd = parse_vnd_int(row[1])
                if code is None or price_vnd is None or code in seen_codes:
                    continue
                seen_codes.add(code)
                candidates.append(
                    PriceCandidate(
                        commodity_code=code,
                        location_code=FuelZoneCode.VUNG_1.value,
                        buy_price_vnd=None,
                        sell_price_vnd=None,
                        retail_price_vnd=price_vnd,
                        effective_date=effective_date,
                        source_published_at=published_vn,
                        crawled_at=crawled_vn,
                        source_url=proof_url,
                        evidence_sha256=sha256,
                    )
                )

        if len(candidates) < 3:
            raise SchemaDriftError(
                f"Saigon Petro fuel canary failed: expected >= 3 Vùng 1 prices, parsed {len(candidates)}"
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

