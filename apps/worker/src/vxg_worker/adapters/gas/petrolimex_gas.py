"""Official Petrolimex Gas adapter (pgas.petrolimex.com.vn)."""

from __future__ import annotations

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import expand_html_table, map_vn_city_code, parse_html
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import parse_vnd_int


class PetrolimexGasAdapter(BaseSourceAdapter):
    """Parses Petrolimex Gas homepage retail table for HN, DN, and HCM (12kg & 48kg)."""

    parser_key = "gas.petrolimex_gas"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        crawled_vn = vn_now()
        effective_date = vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        candidates: list[PriceCandidate] = []
        seen_keys: set[tuple[str, str]] = set()
        found_table = False

        for tbl in tree.css("table"):
            rows = expand_html_table(tbl)
            if not rows:
                continue
            header_joined = " ".join(rows[0]).lower()
            if "12kg" not in header_joined or "48kg" not in header_joined:
                continue
            found_table = True
            for row in rows[1:]:
                if len(row) < 3:
                    continue
                city_code = map_vn_city_code(row[0])
                if city_code is None:
                    continue
                for comm_code, raw_val in (
                    ("GAS_PETROLIMEX_12KG", parse_vnd_int(row[1])),
                    ("GAS_PETROLIMEX_48KG", parse_vnd_int(row[2])),
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

        if not found_table or len(candidates) < 4:
            raise SchemaDriftError(
                f"Petrolimex Gas canary failed: table={found_table}, candidates={len(candidates)}"
            )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "entries_parsed": len(candidates),
            },
        )

