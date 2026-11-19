"""Official SJC Gold JSON API adapter with transparent CafeF WAF fallback."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from vxg_shared.db.models import Source
from vxg_shared.enums import FetchStatus, FetchStrategy
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import to_vn_tz, vn_now, vn_today
from vxg_worker.adapters.base import (
    AdapterParseResult,
    BaseSourceAdapter,
    SchemaDriftError,
    WafFallbackSpec,
)
from vxg_worker.adapters.common import normalize_space, parse_vn_datetime
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate

CAFEF_SJC_FALLBACK_URL = "https://cafef.vn/du-lieu/Ajax/ajaxgoldpricehistory.ashx?index=all"
CAFEF_SJC_FALLBACK_REFERER = "https://cafef.vn/du-lieu/gia-vang-hom-nay/trong-nuoc.chn"
SJC_WAF_FALLBACK_PROVIDER = "CAFEF_GOLD_CROSSCHECK"
SJC_WAF_FALLBACK_NOTE = (
    "Nguồn chính SJC chặn WAF IP nước ngoài (HTTP 403); "
    "dữ liệu được thu thập từ nguồn dự phòng đã xác thực CafeF"
)

_CITIES: tuple[str, ...] = ("HN", "DN", "HCM")

_BRANCH_TO_LOCATION: dict[str, str] = {
    "hồ chí minh": "HCM",
    "tp. hồ chí minh": "HCM",
    "tp.hcm": "HCM",
    "miền bắc": "HN",
    "hà nội": "HN",
    "miền trung": "DN",
    "đà nẵng": "DN",
}


def _parse_cafef_price_vnd(raw_value: Any) -> int:
    """Convert CafeF gold price (million/thousand/raw VND per lượng) to VND int."""
    val = float(raw_value)
    if val < 1_000:
        return int(round(val * 1_000_000))
    if val < 1_000_000:
        return int(round(val * 1_000))
    return int(round(val))


def _is_sjc_bar_entry(name_norm: str) -> bool:
    return name_norm == "sjc" or ("vàng miếng" in name_norm and "sjc" in name_norm)


def _is_sjc_ring_entry(name_norm: str) -> bool:
    return ("nhẫn" in name_norm or "nhan" in name_norm) and "sjc" in name_norm


class SjcJsonAdapter(BaseSourceAdapter):
    """Parses SJC official JSON price service across HN, DN, and HCM."""

    parser_key = "gold.sjc_json"

    def get_waf_fallback(
        self, fetch_result: FetchResult, source: Source
    ) -> WafFallbackSpec | None:
        if source.code != "SJC_OFFICIAL_XML":
            return None
        if fetch_result.http_status == 403 or fetch_result.fetch_status == FetchStatus.BLOCKED:
            return WafFallbackSpec(
                fallback_url=CAFEF_SJC_FALLBACK_URL,
                fetch_strategy=FetchStrategy.HTTP_JSON.value,
                referer=CAFEF_SJC_FALLBACK_REFERER,
                fallback_provider=SJC_WAF_FALLBACK_PROVIDER,
                audit_note=SJC_WAF_FALLBACK_NOTE,
            )
        return None

    def parse_waf_fallback(
        self,
        fetch_result: FetchResult,
        source: Source,
        fallback_spec: WafFallbackSpec,
        primary_result: FetchResult,
    ) -> AdapterParseResult:
        try:
            payload: dict[str, Any] = json.loads(fetch_result.raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchemaDriftError(f"CafeF SJC fallback returned invalid JSON: {exc}") from exc

        histories = payload.get("Data", {}).get("goldPriceWorldHistories")
        if not isinstance(histories, list) or not histories:
            raise SchemaDriftError("CafeF SJC fallback JSON missing Data.goldPriceWorldHistories")

        latest_sjc_bar: dict[str, Any] | None = None
        latest_sjc_ring: dict[str, Any] | None = None
        for item in histories:
            if item.get("buyPrice") is None or item.get("sellPrice") is None:
                continue
            name_norm = normalize_space(str(item.get("name") or "")).lower()
            if latest_sjc_bar is None and _is_sjc_bar_entry(name_norm):
                latest_sjc_bar = item
            elif latest_sjc_ring is None and _is_sjc_ring_entry(name_norm):
                latest_sjc_ring = item
            if latest_sjc_bar is not None and latest_sjc_ring is not None:
                break

        if latest_sjc_bar is None:
            raise SchemaDriftError("CafeF SJC fallback JSON missing domestic SJC record")

        proof_url = fetch_result.final_url or fetch_result.request_url or fallback_spec.fallback_url
        sha256 = fetch_result.content_sha256 or compute_content_sha256(fetch_result.raw_bytes)
        crawled_vn = vn_now()

        parsed_entries: list[tuple[str, dict[str, Any]]] = [("GOLD_SJC_BAR_SJC", latest_sjc_bar)]
        if latest_sjc_ring is not None:
            parsed_entries.append(("GOLD_SJC_RING_9999", latest_sjc_ring))

        def _build_audit_notes() -> dict[str, Any]:
            return {
                "source_detail": fallback_spec.audit_note,
                "waf_fallback": {
                    "used": True,
                    "primary_url": primary_result.request_url,
                    "primary_http_status": primary_result.http_status,
                    "primary_fetch_status": primary_result.fetch_status.value,
                    "fallback_provider": fallback_spec.fallback_provider,
                    "fallback_url": proof_url,
                    "fallback_sha256": sha256,
                    "reason": fallback_spec.audit_note,
                },
            }

        candidates: list[PriceCandidate] = []
        bar_buy_vnd = 0
        bar_sell_vnd = 0
        bar_created_raw = ""

        for commodity_code, entry in parsed_entries:
            buy_vnd = _parse_cafef_price_vnd(entry["buyPrice"])
            sell_vnd = _parse_cafef_price_vnd(entry["sellPrice"])
            created_raw = str(entry.get("createdAt") or "")
            published_vn: datetime | None = None
            if created_raw:
                parsed_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                published_vn = to_vn_tz(parsed_dt)
            effective_date = published_vn.date() if published_vn is not None else vn_today()

            if commodity_code == "GOLD_SJC_BAR_SJC":
                bar_buy_vnd = buy_vnd
                bar_sell_vnd = sell_vnd
                bar_created_raw = created_raw

            for city in _CITIES:
                candidates.append(
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
                        audit_notes=_build_audit_notes(),
                    )
                )

        return AdapterParseResult(
            documents_parsed=1,
            candidates=candidates,
            parser_metadata={
                "waf_fallback_used": True,
                "waf_fallback_provider": fallback_spec.fallback_provider,
                "waf_fallback_url": proof_url,
                "waf_fallback_sha256": sha256,
                "source_detail": fallback_spec.audit_note,
                "sjc_bar_buy_vnd": bar_buy_vnd,
                "sjc_bar_sell_vnd": bar_sell_vnd,
                "sjc_ring_parsed": latest_sjc_ring is not None,
                "created_at": bar_created_raw,
            },
        )

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

