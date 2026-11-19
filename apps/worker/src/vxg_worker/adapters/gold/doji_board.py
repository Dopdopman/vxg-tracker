"""Official DOJI price board adapter (banggia.doji.vn)."""

from __future__ import annotations

from typing import Literal

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    assert_not_soft_404,
)
from vxg_worker.adapters.common import expand_html_table, normalize_space, parse_html
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import normalize_gold_price_to_luong, parse_vnd_int

_DOJI_CITIES: tuple[str, ...] = ("HN", "DN", "HCM")


class DojiBoardAdapter(BaseSourceAdapter):
    """Parses DOJI live price board HTML rendered by Playwright."""

    parser_key = "gold.doji_board"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        page_text_lower = normalize_space(tree.body.text(strip=True)).lower() if tree.body else ""
        source_unit: Literal["THOUSAND_PER_CHI", "THOUSAND_PER_LUONG"] = (
            "THOUSAND_PER_CHI"
            if "nghìn vnd/chỉ" in page_text_lower or "nghìn đ/chỉ" in page_text_lower
            else "THOUSAND_PER_LUONG"
        )

        target_rows: list[list[str]] | None = None
        for tbl in tree.css("table"):
            rows = expand_html_table(tbl)
            joined = " ".join(" ".join(r).lower() for r in rows)
            if "mua vào" in joined and "bán ra" in joined and "vàng miếng sjc" in joined:
                target_rows = rows
                break

        if target_rows is None:
            raise SchemaDriftError("DOJI domestic gold price table not found")

        crawled_vn = vn_now()
        effective_date = vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        parsed_items: dict[str, tuple[int, int]] = {}
        for row in target_rows:
            if len(row) < 4:
                continue
            name_lower = row[1].lower()
            raw_buy = parse_vnd_int(row[2])
            raw_sell = parse_vnd_int(row[3])
            if raw_buy is None or raw_sell is None:
                continue

            # Guard against accidental 10x unit mismatch if page omits explicit unit caption
            eff_unit: Literal["THOUSAND_PER_CHI", "THOUSAND_PER_LUONG"] = (
                "THOUSAND_PER_CHI" if raw_sell < 50_000 else "THOUSAND_PER_LUONG"
            )
            if "nghìn vnd/chỉ" in page_text_lower and raw_sell < 50_000:
                eff_unit = source_unit

            buy_vnd = normalize_gold_price_to_luong(raw_buy, source_unit=eff_unit)
            sell_vnd = normalize_gold_price_to_luong(raw_sell, source_unit=eff_unit)

            if "vàng miếng sjc" in name_lower and "GOLD_SJC_BAR_DOJI" not in parsed_items:
                parsed_items["GOLD_SJC_BAR_DOJI"] = (buy_vnd, sell_vnd)
            elif "nhẫn tròn 9999" in name_lower and "GOLD_RING_9999_DOJI" not in parsed_items:
                parsed_items["GOLD_RING_9999_DOJI"] = (buy_vnd, sell_vnd)

        if not parsed_items:
            raise SchemaDriftError("DOJI canary failed: no SJC bar or 9999 ring rows parsed")

        candidates: list[PriceCandidate] = [
            PriceCandidate(
                commodity_code=commodity_code,
                location_code=city,
                buy_price_vnd=buy_vnd,
                sell_price_vnd=sell_vnd,
                retail_price_vnd=None,
                effective_date=effective_date,
                source_published_at=None,
                crawled_at=crawled_vn,
                source_url=proof_url,
                evidence_sha256=sha256,
            )
            for commodity_code, (buy_vnd, sell_vnd) in parsed_items.items()
            for city in _DOJI_CITIES
        ]

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "detected_unit": source_unit,
                "scope": "nationwide_board",
                "products_parsed": sorted(parsed_items.keys()),
            },
        )

