"""Offline unit tests for SJC_OFFICIAL_XML -> CafeF transparent WAF fallback."""

from __future__ import annotations

import json
from pathlib import Path

from vxg_shared.db.models import Source
from vxg_shared.db.seed import SEED_COMMODITIES, SEED_LOCATIONS, SEED_SOURCES
from vxg_shared.enums import (
    CommodityCategory,
    FetchStatus,
    PriceUnit,
    PricingType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_content_sha256
from vxg_worker.adapters.gold.sjc_json import (
    CAFEF_SJC_FALLBACK_URL,
    SJC_WAF_FALLBACK_NOTE,
    SjcJsonAdapter,
)
from vxg_worker.fetcher.types import FetcherTier, FetchResult
from vxg_worker.validation.guardrails import PriceCandidate, ValidationContext, ValidationEngine

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _source_by_code(code: str, *, source_id: int = 1) -> Source:
    row = next(s for s in SEED_SOURCES if s["code"] == code)
    return Source(
        id=source_id,
        code=row["code"],
        name=row["name"],
        category=row["category"],
        tier=row["tier"],
        base_url=row["base_url"],
        endpoint_url=row["endpoint_url"],
        fetch_strategy=row["fetch_strategy"],
        parser_key=row["parser_key"],
        schedule_cron=row["schedule_cron"],
        timeout_ms=row["timeout_ms"],
        rate_limit_delay_ms=row["rate_limit_delay_ms"],
        is_active=row["is_active"],
    )


def _validate_candidates(candidates: list[PriceCandidate], *, source_id: int = 1) -> list[dict[str, object]]:
    comm_map = {c["code"]: c for c in SEED_COMMODITIES}
    loc_map = {loc["code"]: idx + 1 for idx, loc in enumerate(SEED_LOCATIONS)}
    engine = ValidationEngine()

    notes_list: list[dict[str, object]] = []
    for idx, cand in enumerate(candidates, start=1):
        comm = comm_map[cand.commodity_code]
        ctx = ValidationContext(
            commodity_id=idx,
            commodity_code=comm["code"],
            category=CommodityCategory(comm["category"]),
            unit=PriceUnit(comm["unit"]),
            pricing_type=PricingType(comm["pricing_type"]),
            spec_detail=comm["spec_detail"],
            location_id=loc_map[cand.location_code],
            source_id=source_id,
        )
        decision = engine.validate(cand, ctx)
        assert decision.verification_status == VerificationStatus.VERIFIED, (
            f"Unexpected guardrail status for {cand.commodity_code}/{cand.location_code}: "
            f"{decision.verification_status} ({decision.failure_reason})"
        )
        notes_list.append(decision.validation_notes)
    return notes_list


def test_sjc_adapter_waf_fallback_on_403_uses_cafef_fixture_and_evidence() -> None:
    adapter = SjcJsonAdapter()
    sjc_src = _source_by_code("SJC_OFFICIAL_XML", source_id=1)
    doji_src = _source_by_code("DOJI_BOARD", source_id=2)

    blocked_403 = FetchResult(
        request_url=sjc_src.endpoint_url,
        final_url=sjc_src.endpoint_url,
        http_status=403,
        content_type="text/html",
        raw_bytes=b"<html>403 Forbidden</html>",
        content_sha256=compute_content_sha256(b"<html>403 Forbidden</html>"),
        byte_size=26,
        response_time_ms=150,
        attempts=1,
        tier_used=FetcherTier.PLAYWRIGHT,
        fetch_status=FetchStatus.BLOCKED,
        fallback_triggered=True,
        error_code="HTTP_403",
        error_message="Blocked with HTTP 403",
    )

    spec = adapter.get_waf_fallback(blocked_403, sjc_src)
    assert spec is not None
    assert spec.fallback_url == CAFEF_SJC_FALLBACK_URL
    assert spec.fallback_provider == "CAFEF_GOLD_CROSSCHECK"
    assert spec.audit_note == SJC_WAF_FALLBACK_NOTE

    # Non-403 / non-SJC sources must NOT trigger SJC's WAF fallback.
    not_found_404 = FetchResult(
        request_url=sjc_src.endpoint_url,
        final_url=sjc_src.endpoint_url,
        http_status=404,
        content_type="text/html",
        raw_bytes=b"",
        content_sha256=None,
        byte_size=0,
        response_time_ms=50,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.HTTP_ERROR,
        error_code="HTTP_404",
        error_message="Not found",
    )
    assert adapter.get_waf_fallback(not_found_404, sjc_src) is None
    assert adapter.get_waf_fallback(blocked_403, doji_src) is None

    cafef_bytes = (FIXTURES_DIR / "gold/cafef_gold.json").read_bytes()
    cafef_sha256 = compute_content_sha256(cafef_bytes)
    cafef_result = FetchResult(
        request_url=spec.fallback_url,
        final_url=spec.fallback_url,
        http_status=200,
        content_type="application/json; charset=utf-8",
        raw_bytes=cafef_bytes,
        content_sha256=cafef_sha256,
        byte_size=len(cafef_bytes),
        response_time_ms=85,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.SUCCESS,
    )

    parsed = adapter.parse_waf_fallback(cafef_result, sjc_src, spec, blocked_403)
    assert parsed.documents_parsed == 1
    assert len(parsed.candidates) == 3
    assert parsed.parser_metadata["waf_fallback_used"] is True
    assert parsed.parser_metadata["waf_fallback_provider"] == "CAFEF_GOLD_CROSSCHECK"
    assert parsed.parser_metadata["waf_fallback_url"] == CAFEF_SJC_FALLBACK_URL
    assert parsed.parser_metadata["waf_fallback_sha256"] == cafef_sha256
    assert parsed.parser_metadata["source_detail"] == SJC_WAF_FALLBACK_NOTE
    assert parsed.parser_metadata["sjc_ring_parsed"] is False

    by_loc = {c.location_code: c for c in parsed.candidates}
    assert set(by_loc) == {"HN", "DN", "HCM"}
    for cand in parsed.candidates:
        assert cand.commodity_code == "GOLD_SJC_BAR_SJC"
        assert cand.buy_price_vnd == 133_700_000
        assert cand.sell_price_vnd == 136_700_000
        assert cand.source_url == CAFEF_SJC_FALLBACK_URL
        assert cand.evidence_sha256 == cafef_sha256

    validated_notes = _validate_candidates(parsed.candidates, source_id=sjc_src.id)
    for notes in validated_notes:
        assert notes["source_detail"] == SJC_WAF_FALLBACK_NOTE
        waf_meta = notes["waf_fallback"]
        assert isinstance(waf_meta, dict)
        assert waf_meta["used"] is True
        assert waf_meta["fallback_provider"] == "CAFEF_GOLD_CROSSCHECK"
        assert waf_meta["fallback_url"] == CAFEF_SJC_FALLBACK_URL
        assert waf_meta["fallback_sha256"] == cafef_sha256
        assert waf_meta["primary_http_status"] == 403


def test_sjc_waf_fallback_parses_ring_entry_when_present() -> None:
    adapter = SjcJsonAdapter()
    sjc_src = _source_by_code("SJC_OFFICIAL_XML", source_id=1)

    blocked_403 = FetchResult(
        request_url=sjc_src.endpoint_url,
        final_url=sjc_src.endpoint_url,
        http_status=403,
        content_type="text/html",
        raw_bytes=b"",
        content_sha256=None,
        byte_size=0,
        response_time_ms=100,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.BLOCKED,
        error_code="HTTP_403",
        error_message="Forbidden",
    )
    spec = adapter.get_waf_fallback(blocked_403, sjc_src)
    assert spec is not None

    synthetic_payload = {
        "Success": True,
        "Data": {
            "goldPriceWorldHistories": [
                {
                    "name": "SJC",
                    "buyPrice": 133.7,
                    "sellPrice": 136.7,
                    "createdAt": "2026-11-19T04:00:00Z",
                },
                {
                    "name": "Vàng nhẫn SJC 9999",
                    "buyPrice": 132.5,
                    "sellPrice": 136.5,
                    "createdAt": "2026-11-19T04:00:00Z",
                },
            ]
        },
    }
    raw_bytes = json.dumps(synthetic_payload, ensure_ascii=False).encode("utf-8")
    raw_sha = compute_content_sha256(raw_bytes)

    cafef_result = FetchResult(
        request_url=spec.fallback_url,
        final_url=spec.fallback_url,
        http_status=200,
        content_type="application/json",
        raw_bytes=raw_bytes,
        content_sha256=raw_sha,
        byte_size=len(raw_bytes),
        response_time_ms=40,
        attempts=1,
        tier_used=FetcherTier.HTTPX,
        fetch_status=FetchStatus.SUCCESS,
    )

    parsed = adapter.parse_waf_fallback(cafef_result, sjc_src, spec, blocked_403)
    assert len(parsed.candidates) == 6
    assert parsed.parser_metadata["sjc_ring_parsed"] is True

    ring_hcm = next(
        c
        for c in parsed.candidates
        if c.commodity_code == "GOLD_SJC_RING_9999" and c.location_code == "HCM"
    )
    assert ring_hcm.buy_price_vnd == 132_500_000
    assert ring_hcm.sell_price_vnd == 136_500_000
    assert ring_hcm.source_url == CAFEF_SJC_FALLBACK_URL
    assert ring_hcm.evidence_sha256 == raw_sha

    _validate_candidates(parsed.candidates, source_id=sjc_src.id)
