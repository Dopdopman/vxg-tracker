"""Official PV Gas LPG adapter (pvgaslpg.com.vn/vi/gas-price/Ha-Noi.html)."""

from __future__ import annotations

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import map_vn_city_code, normalize_space, parse_html
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import parse_vnd_int


class PvGasLpgAdapter(BaseSourceAdapter):
    """Parses PV Gas LPG provincial price rows covering HN, DN, and HCM (12kg & 45kg)."""

    parser_key = "gas.pvgas_lpg"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        ro_nodes = tree.css("div.ro")
        if not ro_nodes:
            raise SchemaDriftError("PV Gas LPG page missing div.ro province price rows")

        crawled_vn = vn_now()
        effective_date = vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        candidates: list[PriceCandidate] = []
        seen_keys: set[tuple[str, str]] = set()

        for row_node in ro_nodes:
            direct_divs = [child for child in row_node.iter() if child.tag == "div"]
            if len(direct_divs) < 3:
                continue
            city_code = map_vn_city_code(normalize_space(direct_divs[0].text(strip=True)))
            if city_code is None:
                continue

            for comm_code, raw_val in (
                ("GAS_PVGAS_12KG", parse_vnd_int(direct_divs[1].text(strip=True))),
                ("GAS_PVGAS_45KG", parse_vnd_int(direct_divs[2].text(strip=True))),
            ):
                if raw_val is None or (comm_code, city_code) in seen_keys:
                    continue
                seen_keys.add((comm_code, city_code))
                candidates.append(
                    PriceCandidate(
                        commodity_code=comm_code,
                        location_code=city_code,
                        buy_price_vnd=None,
                        sell_price_vnd=None,
                        retail_price_vnd=raw_val,
                        effective_date=effective_date,
                        source_published_at=None,
                        crawled_at=crawled_vn,
                        source_url=proof_url,
                        evidence_sha256=sha256,
                    )
                )

        if len(candidates) < 4:
            raise SchemaDriftError(
                f"PV Gas LPG canary failed: expected >= 4 city cylinder prices, parsed {len(candidates)}"
            )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "ro_blocks_seen": len(ro_nodes),
                "entries_parsed": len(candidates),
            },
        )

