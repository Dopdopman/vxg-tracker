"""CafeF Tier 2 Cross-Check adapter for domestic SJC gold bar prices."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from vxg_shared.db.models import Source
from vxg_shared.timezone import to_vn_tz, vn_now, vn_today
from vxg_worker.adapters.base import AdapterParseResult, BaseSourceAdapter, SchemaDriftError
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate

_SJC_BAR_COMMODITIES: tuple[str, ...] = (
    "GOLD_SJC_BAR_SJC",
    "GOLD_SJC_BAR_DOJI",
    "GOLD_SJC_BAR_PNJ",
)
_CITIES: tuple[str, ...] = ("HN", "DN", "HCM")


class CafefGoldCrossCheckAdapter(BaseSourceAdapter):
    """Parses CafeF ajaxgoldpricehistory.ashx JSON for Tier 2 SJC gold cross-checking."""

    parser_key = "gold.cafef_gold"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        try:
            payload: dict[str, Any] = json.loads(fetch_result.raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchemaDriftError(f"CafeF gold returned invalid JSON: {exc}") from exc

        histories = payload.get("Data", {}).get("goldPriceWorldHistories")
        if not isinstance(histories, list) or not histories:
            raise SchemaDriftError("CafeF JSON missing Data.goldPriceWorldHistories")

        latest_sjc = next(
            (item for item in histories if str(item.get("name") or "").upper() == "SJC"),
            None,
        )
        if latest_sjc is None:
            raise SchemaDriftError("CafeF JSON missing domestic SJC record")

        buy_million = latest_sjc.get("buyPrice")
        sell_million = latest_sjc.get("sellPrice")
        if buy_million is None or sell_million is None:
            raise SchemaDriftError("CafeF SJC entry missing buyPrice or sellPrice")

        buy_vnd = int(round(float(buy_million) * 1_000_000))
        sell_vnd = int(round(float(sell_million) * 1_000_000))

        created_raw = str(latest_sjc.get("createdAt") or "")
        published_vn: datetime | None = None
        if created_raw:
            parsed_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            published_vn = to_vn_tz(parsed_dt)

        crawled_vn = vn_now()
        effective_date = published_vn.date() if published_vn is not None else vn_today()
        sha256 = fetch_result.content_sha256 or ""
        proof_url = source.base_url or fetch_result.final_url or fetch_result.request_url

        candidates = [
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
            for commodity_code in _SJC_BAR_COMMODITIES
            for city in _CITIES
        ]

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "sjc_buy_vnd": buy_vnd,
                "sjc_sell_vnd": sell_vnd,
                "created_at": created_raw,
            },
        )

