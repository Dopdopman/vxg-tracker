"""Official SJC Gold JSON API adapter (sjc.com.vn/GoldPrice/Services/PriceService.ashx)."""

from __future__ import annotations

import json
from typing import Any

from vxg_shared.db.models import Source
from vxg_shared.timezone import vn_now, vn_today
from vxg_worker.adapters.base import AdapterParseResult, BaseSourceAdapter, SchemaDriftError
from vxg_worker.adapters.common import normalize_space, parse_vn_datetime
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate

_BRANCH_TO_LOCATION: dict[str, str] = {
    "hồ chí minh": "HCM",
    "tp. hồ chí minh": "HCM",
    "tp.hcm": "HCM",
    "miền bắc": "HN",
    "hà nội": "HN",
    "miền trung": "DN",
    "đà nẵng": "DN",
}


class SjcJsonAdapter(BaseSourceAdapter):
    """Parses SJC official JSON price service across HN, DN, and HCM."""

    parser_key = "gold.sjc_json"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        try:
            payload: dict[str, Any] = json.loads(fetch_result.raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchemaDriftError(f"SJC returned invalid JSON: {exc}") from exc

        if not payload.get("success") or not isinstance(payload.get("data"), list):
            raise SchemaDriftError("SJC JSON missing success=true or data[] list")

        latest_date_str = str(payload.get("latestDate") or "")
        published_vn = parse_vn_datetime(latest_date_str)
        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = source.base_url or fetch_result.final_url or fetch_result.request_url

        candidates: list[PriceCandidate] = []
        seen_keys: set[tuple[str, str]] = set()

        for item in payload["data"]:
            type_name = normalize_space(str(item.get("TypeName") or "")).lower()
            branch_name = normalize_space(str(item.get("BranchName") or "")).lower()

            location_code = _BRANCH_TO_LOCATION.get(branch_name)
            if location_code is None:
                continue

            commodity_code: str | None = None
            if "vàng sjc 1l" in type_name:
                commodity_code = "GOLD_SJC_BAR_SJC"
            elif "vàng nhẫn sjc 99,99%" in type_name and "1 chỉ, 2 chỉ, 5 chỉ" in type_name:
                commodity_code = "GOLD_SJC_RING_9999"

            if commodity_code is None or (commodity_code, location_code) in seen_keys:
                continue

            buy_val = item.get("BuyValue")
            sell_val = item.get("SellValue")
            if buy_val is None or sell_val is None:
                continue

            seen_keys.add((commodity_code, location_code))
            candidates.append(
                PriceCandidate(
                    commodity_code=commodity_code,
                    location_code=location_code,
                    buy_price_vnd=int(round(float(buy_val))),
                    sell_price_vnd=int(round(float(sell_val))),
                    retail_price_vnd=None,
                    effective_date=effective_date,
                    source_published_at=published_vn,
                    crawled_at=crawled_vn,
                    source_url=proof_url,
                    evidence_sha256=sha256,
                )
            )

        if len(candidates) < 3:
            raise SchemaDriftError(
                f"SJC JSON canary failed: expected >= 3 regional prices, parsed {len(candidates)}"
            )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "latest_date": latest_date_str,
                "raw_rows_count": len(payload["data"]),
            },
        )

