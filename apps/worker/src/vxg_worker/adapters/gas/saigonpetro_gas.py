"""Saigon Petro 2-step retail Gas notice adapter (saigonpetro.com.vn/gia-ban-le)."""

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
from vxg_worker.adapters.common import (
    normalize_space,
    parse_html,
    parse_vn_datetime,
    resolve_absolute_url,
)
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import parse_vnd_int

_GAS_12KG_RE = re.compile(r"12\s*kg[^:]{0,60}:\s*([\d\.]+)\s*đồng", re.I)
_GAS_45KG_RE = re.compile(r"45\s*kg[^:]{0,60}:\s*([\d\.]+)\s*đồng", re.I)


class SaigonPetroGasAdapter(BaseSourceAdapter):
    """2-step adapter: finds latest gas notice URL then extracts 12kg & 45kg prices for HCM."""

    parser_key = "gas.saigonpetro_gas"

    def extract_detail_url(self, listing_result: FetchResult, source: Source) -> str | None:
        assert_not_soft_404(listing_result, source)
        tree = parse_html(listing_result.raw_bytes)
        base = listing_result.final_url or source.base_url
        for anchor in tree.css("a"):
            href = (anchor.attributes.get("href") or "").strip()
            if "gia-ban-le-gas-tu-ngay-" in href:
                return resolve_absolute_url(href, base)
        return None

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("h1") or tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        toc = tree.css_first("#toc-content")
        if toc is None:
            raise SchemaDriftError("Saigon Petro gas notice missing #toc-content container")

        toc_text = normalize_space(toc.text(strip=True))
        published_vn = parse_vn_datetime(doc_title) or parse_vn_datetime(toc_text)
        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        price_12kg: int | None = None
        price_45kg: int | None = None
        for node in toc.css("p, li"):
            line = normalize_space(node.text(strip=True))
            if price_12kg is None and (m12 := _GAS_12KG_RE.search(line)):
                price_12kg = parse_vnd_int(m12.group(1))
            if price_45kg is None and (m45 := _GAS_45KG_RE.search(line)):
                price_45kg = parse_vnd_int(m45.group(1))

        candidates: list[PriceCandidate] = []
        for comm_code, price_val in (
            ("GAS_SAIGONPETRO_12KG", price_12kg),
            ("GAS_SAIGONPETRO_45KG", price_45kg),
        ):
            if price_val is None:
                continue
            candidates.append(
                PriceCandidate(
                    commodity_code=comm_code,
                    location_code="HCM",
                    buy_price_vnd=None,
                    sell_price_vnd=None,
                    retail_price_vnd=price_val,
                    effective_date=effective_date,
                    source_published_at=published_vn,
                    crawled_at=crawled_vn,
                    source_url=proof_url,
                    evidence_sha256=sha256,
                )
            )

        if len(candidates) < 2:
            raise SchemaDriftError(
                f"Saigon Petro gas canary failed: expected 12kg and 45kg, parsed {len(candidates)}"
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

