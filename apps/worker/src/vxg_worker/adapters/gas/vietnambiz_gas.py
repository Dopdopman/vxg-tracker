"""VietnamBiz 2-step Tier 2 Cross-Check adapter for retail LPG gas prices."""

from __future__ import annotations

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import (
    expand_html_table,
    map_vn_city_code,
    parse_html,
    parse_vn_datetime,
    resolve_absolute_url,
)
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import parse_vnd_int


class VietnamBizGasCrossCheckAdapter(BaseSourceAdapter):
    """2-step adapter: finds latest daily VietnamBiz gas roundup then parses comparison tables."""

    parser_key = "gas.vietnambiz_gas"

    def extract_detail_url(self, listing_result: FetchResult, source: Source) -> str | None:
        assert_not_soft_404(listing_result, source)
        tree = parse_html(listing_result.raw_bytes)
        base = listing_result.final_url or source.base_url
        for anchor in tree.css("h2 a, h3 a, .title a"):
            href = (anchor.attributes.get("href") or "").strip()
            if "gia-gas-" in href and href.endswith(".htm"):
                return resolve_absolute_url(href, base)
        return None

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("h1") or tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        pub_node = tree.css_first("span[data-role=publishdate]")
        published_vn = parse_vn_datetime(pub_node.text(strip=True)) if pub_node else None
        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        candidates: list[PriceCandidate] = []
        seen_keys: set[tuple[str, str]] = set()

        def add_candidate(comm_code: str, city_code: str, raw_price: str) -> None:
            price_val = parse_vnd_int(raw_price)
            if price_val is None or (comm_code, city_code) in seen_keys:
                return
            seen_keys.add((comm_code, city_code))
            candidates.append(
                PriceCandidate(
                    commodity_code=comm_code,
                    location_code=city_code,
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

        for tbl in tree.css("table"):
            rows = expand_html_table(tbl)
            if len(rows) < 3:
                continue

            header_text = " ".join(" ".join(r).lower() for r in rows[:2])

            # Table A: Brand comparison table (Petrolimex, Saigon Petro, PV GAS)
            if "thương hiệu" in header_text and "bình 12kg" in header_text:
                for row in rows[2:]:
                    if len(row) < 5:
                        continue
                    brand_lower = row[0].lower()
                    city_code = map_vn_city_code(row[1])
                    if "petrolimex" in brand_lower and city_code:
                        add_candidate("GAS_PETROLIMEX_12KG", city_code, row[2])
                        add_candidate("GAS_PETROLIMEX_48KG", city_code, row[4])
                    elif "saigon petro" in brand_lower:
                        add_candidate("GAS_SAIGONPETRO_12KG", city_code or "HCM", row[2])
                        add_candidate("GAS_SAIGONPETRO_45KG", city_code or "HCM", row[3])
                    elif "pv gas" in brand_lower and city_code:
                        add_candidate("GAS_PVGAS_12KG", city_code, row[2])
                        add_candidate("GAS_PVGAS_45KG", city_code, row[3])
                continue

            # Table B: Regional Petrolimex table (Miền Bắc / Hà Nội / Đà Nẵng / TP HCM)
            if "khu vực" in header_text and "bình 12kg" in header_text and "bình 48kg" in header_text:
                for row in rows[2:]:
                    if len(row) < 4:
                        continue
                    city_code = map_vn_city_code(row[1])
                    if city_code is None:
                        continue
                    add_candidate("GAS_PETROLIMEX_12KG", city_code, row[2])
                    add_candidate("GAS_PETROLIMEX_48KG", city_code, row[3])

        if len(candidates) < 4:
            raise SchemaDriftError(
                f"VietnamBiz gas canary failed: expected >= 4 cross-check prices, parsed {len(candidates)}"
            )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "entries_parsed": len(candidates),
            },
        )

