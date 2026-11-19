"""Official PNJ gold price adapter (pnj.com.vn/site/gia-vang)."""

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
from vxg_worker.adapters.common import (
    expand_html_table,
    normalize_space,
    parse_html,
    parse_vn_datetime,
)
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate
from vxg_worker.validation.normalizer import normalize_gold_price_to_luong, parse_vnd_int

_PNJ_CITIES: tuple[str, ...] = ("HCM", "HN", "DN")


class PnjHtmlAdapter(BaseSourceAdapter):
    """Parses PNJ gold table (ĐVT: 1.000đ/Chỉ) and normalizes to VND/lượng."""

    parser_key = "gold.pnj_html"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)
        tree = parse_html(fetch_result.raw_bytes)
        title_node = tree.css_first("title")
        doc_title = title_node.text(strip=True)[:200] if title_node else ""

        body_text = normalize_space(tree.body.text(strip=True)) if tree.body else ""
        published_vn = parse_vn_datetime(body_text)

        target_rows: list[list[str]] | None = None
        source_unit: Literal["THOUSAND_PER_CHI", "THOUSAND_PER_LUONG"] = "THOUSAND_PER_CHI"
        for tbl in tree.css("table"):
            rows = expand_html_table(tbl)
            if not rows:
                continue
            header_joined = " ".join(rows[0]).lower()
            table_joined = " ".join(" ".join(r).lower() for r in rows)
            if "giá mua" in header_joined and "giá bán" in header_joined and "sjc" in table_joined:
                target_rows = rows
                if "lượng" in header_joined and "chỉ" not in header_joined:
                    source_unit = "THOUSAND_PER_LUONG"
                break

        if target_rows is None:
            raise SchemaDriftError("PNJ gold price table not found")

        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = fetch_result.final_url or fetch_result.request_url

        parsed_items: dict[str, tuple[int, int]] = {}
        for row in target_rows[1:]:
            if len(row) < 3:
                continue
            name_lower = row[0].lower()
            raw_buy = parse_vnd_int(row[1])
            raw_sell = parse_vnd_int(row[2])
            if raw_buy is None or raw_sell is None:
                continue

            buy_vnd = normalize_gold_price_to_luong(raw_buy, source_unit=source_unit)
            sell_vnd = normalize_gold_price_to_luong(raw_sell, source_unit=source_unit)

            if "vàng miếng sjc" in name_lower and "GOLD_SJC_BAR_PNJ" not in parsed_items:
                parsed_items["GOLD_SJC_BAR_PNJ"] = (buy_vnd, sell_vnd)
            elif "nhẫn trơn pnj 999.9" in name_lower and "GOLD_RING_9999_PNJ" not in parsed_items:
                parsed_items["GOLD_RING_9999_PNJ"] = (buy_vnd, sell_vnd)

        if not parsed_items:
            raise SchemaDriftError("PNJ canary failed: no SJC bar or PNJ 999.9 ring rows parsed")

        candidates: list[PriceCandidate] = [
            PriceCandidate(
                commodity_code=commodity_code,
                location_code=city,
                buy_price_vnd=buy_vnd,
                sell_price_vnd=sell_vnd,
                retail_price_vnd=None,
                effective_date=effective_date,
                source_published_at=published_vn,
                crawled_at=crawled_vn,
                source_url=proof_url,
                evidence_sha256=sha256,
            )
            for commodity_code, (buy_vnd, sell_vnd) in parsed_items.items()
            for city in _PNJ_CITIES
        ]

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "document_title": doc_title,
                "source_unit": source_unit,
                "scope": "nationwide_board",
                "products_parsed": sorted(parsed_items.keys()),
            },
        )

