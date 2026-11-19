"""6-Layer validation engine preventing invalid or anomalous prices from reaching users."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from vxg_shared.enums import (
    AlertSeverity,
    AlertType,
    CommodityCategory,
    PriceUnit,
    PricingType,
    VerificationStatus,
)
from vxg_shared.hashing import compute_price_idempotency_key
from vxg_shared.timezone import to_vn_tz

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_GOLD_BOUNDS_VND = (30_000_000, 300_000_000)
_FUEL_BOUNDS_VND = (10_000, 60_000)
_GAS_12KG_BOUNDS_VND = (200_000, 1_500_000)
_GAS_INDUSTRIAL_BOUNDS_VND = (800_000, 6_000_000)
_GAS_FALLBACK_BOUNDS_VND = (200_000, 6_000_000)


@dataclass(slots=True)
class PriceCandidate:
    """Normalized candidate emitted by a source adapter prior to validation."""

    commodity_code: str
    location_code: str
    buy_price_vnd: Any
    sell_price_vnd: Any
    retail_price_vnd: Any
    effective_date: date
    source_published_at: datetime | None
    crawled_at: datetime
    source_url: str
    evidence_sha256: str
    cross_check_source_id: int | None = None
    cross_check_delta_pct: Decimal | None = None
    audit_notes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreviousVerifiedPrice:
    """Most recent clean VERIFIED/CROSS_CHECKED price for historical delta checks."""

    buy_price_vnd: int | None
    sell_price_vnd: int | None
    retail_price_vnd: int | None
    effective_date: date
    crawled_at: datetime


@dataclass(slots=True)
class ValidationContext:
    """Catalog metadata and historical baseline required to validate one candidate."""

    commodity_id: int
    commodity_code: str
    category: CommodityCategory
    unit: PriceUnit
    pricing_type: PricingType
    spec_detail: str | None
    location_id: int
    source_id: int
    previous_verified: PreviousVerifiedPrice | None = None


@dataclass(slots=True)
class ValidationDecision:
    """Result of passing a candidate through all 6 guardrail layers."""

    verification_status: VerificationStatus
    idempotency_key: str | None
    buy_price_vnd: int | None
    sell_price_vnd: int | None
    retail_price_vnd: int | None
    effective_date: date
    source_published_at_vn: datetime | None
    crawled_at_vn: datetime | None
    alert_type: AlertType | None = None
    alert_severity: AlertSeverity | None = None
    failure_reason: str | None = None
    validation_notes: dict[str, Any] = field(default_factory=dict)


def _is_strict_int_or_none(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _resolve_bounds(ctx: ValidationContext) -> tuple[int, int]:
    if ctx.category == CommodityCategory.GOLD:
        return _GOLD_BOUNDS_VND
    if ctx.category == CommodityCategory.FUEL:
        return _FUEL_BOUNDS_VND
    code_upper = ctx.commodity_code.upper()
    if "12KG" in code_upper:
        return _GAS_12KG_BOUNDS_VND
    if "45KG" in code_upper or "48KG" in code_upper:
        return _GAS_INDUSTRIAL_BOUNDS_VND
    return _GAS_FALLBACK_BOUNDS_VND


class ValidationEngine:
    """Implements the 6-Layer Validation Guardrails defined in the architecture plan."""

    def __init__(
        self,
        *,
        spike_threshold_pct: float = 15.0,
        gold_max_spread_pct: float = 10.0,
    ) -> None:
        self._spike_threshold_pct = spike_threshold_pct
        self._gold_max_spread_pct = gold_max_spread_pct

    def validate(self, candidate: PriceCandidate, ctx: ValidationContext) -> ValidationDecision:
        notes: dict[str, Any] = {"layers": {}}
        for note_key, note_val in candidate.audit_notes.items():
            if note_key != "layers":
                notes[note_key] = note_val

        # --- Layer 1: Structure & Types ---
        url_ok = isinstance(candidate.source_url, str) and candidate.source_url.startswith(
            ("http://", "https://")
        )
        hash_ok = isinstance(candidate.evidence_sha256, str) and bool(
            _SHA256_RE.match(candidate.evidence_sha256)
        )
        date_ok = isinstance(candidate.effective_date, date) and not isinstance(
            candidate.effective_date, datetime
        )
        types_ok = (
            _is_strict_int_or_none(candidate.buy_price_vnd)
            and _is_strict_int_or_none(candidate.sell_price_vnd)
            and _is_strict_int_or_none(candidate.retail_price_vnd)
        )
        if not (url_ok and hash_ok and date_ok and types_ok):
            notes["layers"]["1_structure_types"] = "FAILED"
            return self._reject(
                candidate=candidate,
                reason="Layer 1 failed: invalid structure, non-integer price, URL, date, or SHA-256",
                notes=notes,
            )
        notes["layers"]["1_structure_types"] = "PASSED"

        buy: int | None = candidate.buy_price_vnd
        sell: int | None = candidate.sell_price_vnd
        retail: int | None = candidate.retail_price_vnd

        # --- Layer 2: Positive Non-Null Values ---
        non_null_prices = [p for p in (buy, sell, retail) if p is not None]
        if not non_null_prices or any(p <= 0 for p in non_null_prices):
            notes["layers"]["2_positive_non_null"] = "FAILED"
            return self._reject(
                candidate=candidate,
                reason="Layer 2 failed: missing price or price <= 0",
                notes=notes,
            )
        notes["layers"]["2_positive_non_null"] = "PASSED"

        # --- Layer 3 (Shape check) & Layer 6 (Timezone awareness check before idempotency) ---
        if ctx.pricing_type == PricingType.BUY_SELL:
            if buy is None or sell is None or retail is not None:
                notes["layers"]["3_business_logic"] = "FAILED_SHAPE"
                return self._reject(
                    candidate=candidate,
                    reason="Layer 3 failed: BUY_SELL commodity requires both buy and sell prices",
                    notes=notes,
                )
        elif retail is None or buy is not None or sell is not None:
            notes["layers"]["3_business_logic"] = "FAILED_SHAPE"
            return self._reject(
                candidate=candidate,
                reason="Layer 3 failed: SINGLE_RETAIL commodity requires retail_price_vnd only",
                notes=notes,
            )

        try:
            crawled_vn = to_vn_tz(candidate.crawled_at)
            published_vn = (
                to_vn_tz(candidate.source_published_at)
                if candidate.source_published_at is not None
                else None
            )
        except ValueError as exc:
            notes["layers"]["6_timezone_idempotency"] = "FAILED_NAIVE_DATETIME"
            return self._reject(
                candidate=candidate,
                reason=f"Layer 6 failed: {exc}",
                notes=notes,
            )

        idempotency_key = compute_price_idempotency_key(
            commodity_id=ctx.commodity_id,
            location_id=ctx.location_id,
            source_id=ctx.source_id,
            effective_date=candidate.effective_date,
            source_published_at=published_vn,
            buy_price_vnd=buy,
            sell_price_vnd=sell,
            retail_price_vnd=retail,
        )

        # --- Layer 3: Business Logic (Gold Sell >= Buy & Spread <= Max) ---
        if ctx.pricing_type == PricingType.BUY_SELL:
            assert buy is not None and sell is not None
            if sell < buy:
                notes["layers"]["3_business_logic"] = "FLAGGED_SELL_LT_BUY"
                return self._flag(
                    candidate=candidate,
                    idempotency_key=idempotency_key,
                    published_vn=published_vn,
                    crawled_vn=crawled_vn,
                    alert_type=AlertType.VALIDATION_ERROR,
                    severity=AlertSeverity.HIGH,
                    reason=f"Layer 3 flagged: sell_price_vnd ({sell}) < buy_price_vnd ({buy})",
                    notes=notes,
                )
            spread_pct = ((sell - buy) / sell) * 100.0
            notes["spread_pct"] = round(spread_pct, 3)
            if spread_pct > self._gold_max_spread_pct:
                notes["layers"]["3_business_logic"] = "FLAGGED_EXCESS_SPREAD"
                return self._flag(
                    candidate=candidate,
                    idempotency_key=idempotency_key,
                    published_vn=published_vn,
                    crawled_vn=crawled_vn,
                    alert_type=AlertType.VALIDATION_ERROR,
                    severity=AlertSeverity.HIGH,
                    reason=(
                        f"Layer 3 flagged: buy/sell spread {spread_pct:.2f}% exceeds "
                        f"limit {self._gold_max_spread_pct:.2f}%"
                    ),
                    notes=notes,
                )
        notes["layers"]["3_business_logic"] = "PASSED"

        # --- Layer 4: Reasonable Commodity Bounds ---
        min_vnd, max_vnd = _resolve_bounds(ctx)
        notes["bounds_vnd"] = [min_vnd, max_vnd]
        for price_val in non_null_prices:
            if price_val < min_vnd or price_val > max_vnd:
                notes["layers"]["4_reasonable_bounds"] = "FLAGGED"
                return self._flag(
                    candidate=candidate,
                    idempotency_key=idempotency_key,
                    published_vn=published_vn,
                    crawled_vn=crawled_vn,
                    alert_type=AlertType.VALIDATION_ERROR,
                    severity=AlertSeverity.HIGH,
                    reason=(
                        f"Layer 4 flagged: price {price_val} VND outside bounds "
                        f"[{min_vnd}, {max_vnd}] for {ctx.commodity_code}"
                    ),
                    notes=notes,
                )
        notes["layers"]["4_reasonable_bounds"] = "PASSED"

        # --- Layer 5: Historical Spike Detection vs Latest VERIFIED ---
        if ctx.previous_verified is not None:
            deltas: list[float] = []
            prev = ctx.previous_verified
            for cur_v, prev_v in (
                (buy, prev.buy_price_vnd),
                (sell, prev.sell_price_vnd),
                (retail, prev.retail_price_vnd),
            ):
                if cur_v is not None and prev_v is not None and prev_v > 0:
                    deltas.append(abs(cur_v - prev_v) / prev_v * 100.0)
            max_delta = max(deltas) if deltas else 0.0
            notes["prev_delta_pct"] = round(max_delta, 3)
            if max_delta > self._spike_threshold_pct:
                notes["layers"]["5_historical_delta"] = "FLAGGED_SPIKE"
                return self._flag(
                    candidate=candidate,
                    idempotency_key=idempotency_key,
                    published_vn=published_vn,
                    crawled_vn=crawled_vn,
                    alert_type=AlertType.PRICE_SPIKE,
                    severity=AlertSeverity.CRITICAL,
                    reason=(
                        f"Layer 5 flagged: price jump {max_delta:.2f}% exceeds "
                        f"threshold {self._spike_threshold_pct:.2f}%"
                    ),
                    notes=notes,
                )
            notes["layers"]["5_historical_delta"] = "PASSED"
        else:
            notes["layers"]["5_historical_delta"] = "SKIPPED_NO_BASELINE"

        # --- Layer 6: VN Time Integrity & Future Timestamp Guard ---
        if candidate.effective_date < date(2020, 1, 1) or (
            candidate.effective_date > crawled_vn.date() + timedelta(days=1)
        ):
            notes["layers"]["6_timezone_idempotency"] = "FLAGGED_BAD_EFFECTIVE_DATE"
            return self._flag(
                candidate=candidate,
                idempotency_key=idempotency_key,
                published_vn=published_vn,
                crawled_vn=crawled_vn,
                alert_type=AlertType.VALIDATION_ERROR,
                severity=AlertSeverity.MEDIUM,
                reason=f"Layer 6 flagged: implausible effective_date {candidate.effective_date}",
                notes=notes,
            )

        if published_vn is not None and published_vn > crawled_vn + timedelta(hours=2):
            notes["layers"]["6_timezone_idempotency"] = "FLAGGED_FUTURE_PUBLISHED_AT"
            return self._flag(
                candidate=candidate,
                idempotency_key=idempotency_key,
                published_vn=published_vn,
                crawled_vn=crawled_vn,
                alert_type=AlertType.VALIDATION_ERROR,
                severity=AlertSeverity.MEDIUM,
                reason=(
                    f"Layer 6 flagged: source_published_at {published_vn.isoformat()} "
                    f"is too far ahead of crawled_at {crawled_vn.isoformat()}"
                ),
                notes=notes,
            )

        notes["layers"]["6_timezone_idempotency"] = "PASSED"

        # --- Cross-Check Reconciliation (Tier 1 vs Tier 2) ---
        status = VerificationStatus.VERIFIED
        if candidate.cross_check_source_id is not None:
            if candidate.cross_check_delta_pct is not None:
                delta_abs = abs(float(candidate.cross_check_delta_pct))
                notes["cross_check_delta_pct"] = round(delta_abs, 3)
                if delta_abs > 5.0:
                    notes["cross_check"] = "FLAGGED_MISMATCH"
                    return self._flag(
                        candidate=candidate,
                        idempotency_key=idempotency_key,
                        published_vn=published_vn,
                        crawled_vn=crawled_vn,
                        alert_type=AlertType.CROSS_CHECK_MISMATCH,
                        severity=AlertSeverity.HIGH,
                        reason=(
                            f"Cross-check mismatch: delta {delta_abs:.2f}% exceeds 5.00% "
                            f"against source_id={candidate.cross_check_source_id}"
                        ),
                        notes=notes,
                    )
                tolerance = 0.5 if ctx.category == CommodityCategory.FUEL else 1.5
                if delta_abs <= tolerance:
                    status = VerificationStatus.CROSS_CHECKED
                    notes["cross_check"] = "MATCHED"
                else:
                    notes["cross_check"] = "WITHIN_5PCT_NOT_EXACT"
            else:
                status = VerificationStatus.CROSS_CHECKED
                notes["cross_check"] = "MATCHED"

        return ValidationDecision(
            verification_status=status,
            idempotency_key=idempotency_key,
            buy_price_vnd=buy,
            sell_price_vnd=sell,
            retail_price_vnd=retail,
            effective_date=candidate.effective_date,
            source_published_at_vn=published_vn,
            crawled_at_vn=crawled_vn,
            validation_notes=notes,
        )

    def _reject(
        self,
        *,
        candidate: PriceCandidate,
        reason: str,
        notes: dict[str, Any],
    ) -> ValidationDecision:
        return ValidationDecision(
            verification_status=VerificationStatus.REJECTED,
            idempotency_key=None,
            buy_price_vnd=None,
            sell_price_vnd=None,
            retail_price_vnd=None,
            effective_date=candidate.effective_date,
            source_published_at_vn=None,
            crawled_at_vn=None,
            alert_type=AlertType.VALIDATION_ERROR,
            alert_severity=AlertSeverity.MEDIUM,
            failure_reason=reason,
            validation_notes=notes,
        )

    def _flag(
        self,
        *,
        candidate: PriceCandidate,
        idempotency_key: str,
        published_vn: datetime | None,
        crawled_vn: datetime,
        alert_type: AlertType,
        severity: AlertSeverity,
        reason: str,
        notes: dict[str, Any],
    ) -> ValidationDecision:
        return ValidationDecision(
            verification_status=VerificationStatus.FLAGGED,
            idempotency_key=idempotency_key,
            buy_price_vnd=candidate.buy_price_vnd,
            sell_price_vnd=candidate.sell_price_vnd,
            retail_price_vnd=candidate.retail_price_vnd,
            effective_date=candidate.effective_date,
            source_published_at_vn=published_vn,
            crawled_at_vn=crawled_vn,
            alert_type=alert_type,
            alert_severity=severity,
            failure_reason=reason,
            validation_notes=notes,
        )
