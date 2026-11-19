"""Database persistence helpers for verified, quarantined, and rejected price candidates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.models import AnomalyAlert, Commodity, Location, PriceRecord
from vxg_shared.enums import AlertSeverity, AlertStatus, AlertType, VerificationStatus
from vxg_shared.timezone import vn_now
from vxg_worker.validation.guardrails import (
    PreviousVerifiedPrice,
    PriceCandidate,
    ValidationDecision,
)

PersistOutcome = Literal["VERIFIED", "FLAGGED", "UNCHANGED", "REJECTED"]

_VERIFIED_STATUSES = (
    VerificationStatus.VERIFIED.value,
    VerificationStatus.CROSS_CHECKED.value,
    VerificationStatus.OVERRIDDEN_MANUAL.value,
)


@dataclass(slots=True)
class CatalogLookup:
    """In-memory lookup maps of active commodities and locations for a crawl run."""

    commodities_by_code: dict[str, Commodity]
    locations_by_code: dict[str, Location]


async def load_catalog_lookup(session: AsyncSession) -> CatalogLookup:
    """Load active commodities and locations keyed by unique code."""
    commodities = (
        await session.execute(select(Commodity).where(Commodity.is_active.is_(True)))
    ).scalars()
    locations = (
        await session.execute(select(Location).where(Location.is_active.is_(True)))
    ).scalars()
    return CatalogLookup(
        commodities_by_code={c.code: c for c in commodities},
        locations_by_code={loc.code: loc for loc in locations},
    )


async def get_latest_verified_price(
    session: AsyncSession,
    *,
    commodity_id: int,
    location_id: int,
) -> PreviousVerifiedPrice | None:
    """Query the latest clean VERIFIED price using idx_price_records_latest_verified."""
    stmt = (
        select(PriceRecord)
        .where(
            PriceRecord.commodity_id == commodity_id,
            PriceRecord.location_id == location_id,
            PriceRecord.verification_status.in_(_VERIFIED_STATUSES),
        )
        .order_by(
            PriceRecord.effective_date.desc(),
            func.coalesce(PriceRecord.source_published_at, PriceRecord.crawled_at).desc(),
            PriceRecord.crawled_at.desc(),
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return PreviousVerifiedPrice(
        buy_price_vnd=row.buy_price_vnd,
        sell_price_vnd=row.sell_price_vnd,
        retail_price_vnd=row.retail_price_vnd,
        effective_date=row.effective_date,
        crawled_at=row.crawled_at,
    )


async def persist_validated_candidate(
    session: AsyncSession,
    *,
    candidate: PriceCandidate,
    decision: ValidationDecision,
    commodity_id: int,
    location_id: int,
    source_id: int,
    crawl_run_id: uuid.UUID,
    source_log_id: uuid.UUID,
) -> PersistOutcome:
    """Persist a validated decision with strict quarantine & idempotency guarantees."""
    if decision.verification_status == VerificationStatus.REJECTED:
        alert = AnomalyAlert(
            crawl_run_id=crawl_run_id,
            source_log_id=source_log_id,
            price_record_id=None,
            alert_type=(decision.alert_type or AlertType.VALIDATION_ERROR).value,
            severity=(decision.alert_severity or AlertSeverity.MEDIUM).value,
            status=AlertStatus.OPEN.value,
            message=decision.failure_reason or "Price candidate rejected by validation guardrails",
            payload={
                "commodity_code": candidate.commodity_code,
                "location_code": candidate.location_code,
                "source_url": candidate.source_url,
                "validation_notes": decision.validation_notes,
            },
        )
        session.add(alert)
        return "REJECTED"

    assert decision.idempotency_key is not None
    assert decision.crawled_at_vn is not None

    existing_id = (
        await session.execute(
            select(PriceRecord.id).where(PriceRecord.idempotency_key == decision.idempotency_key)
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        await session.execute(
            update(PriceRecord)
            .where(PriceRecord.id == existing_id)
            .values(last_verified_at=vn_now())
        )
        return "UNCHANGED"

    record = PriceRecord(
        commodity_id=commodity_id,
        location_id=location_id,
        source_id=source_id,
        cross_check_source_id=candidate.cross_check_source_id,
        crawl_run_id=crawl_run_id,
        source_log_id=source_log_id,
        buy_price_vnd=decision.buy_price_vnd,
        sell_price_vnd=decision.sell_price_vnd,
        retail_price_vnd=decision.retail_price_vnd,
        effective_date=decision.effective_date,
        source_published_at=decision.source_published_at_vn,
        crawled_at=decision.crawled_at_vn,
        last_verified_at=decision.crawled_at_vn,
        source_url=candidate.source_url,
        evidence_sha256=candidate.evidence_sha256,
        verification_status=decision.verification_status.value,
        cross_check_delta_pct=candidate.cross_check_delta_pct,
        validation_notes=decision.validation_notes,
        idempotency_key=decision.idempotency_key,
    )
    session.add(record)
    await session.flush()

    if decision.verification_status == VerificationStatus.FLAGGED:
        alert = AnomalyAlert(
            crawl_run_id=crawl_run_id,
            source_log_id=source_log_id,
            price_record_id=record.id,
            alert_type=(decision.alert_type or AlertType.VALIDATION_ERROR).value,
            severity=(decision.alert_severity or AlertSeverity.HIGH).value,
            status=AlertStatus.OPEN.value,
            message=decision.failure_reason or "Price candidate flagged for quarantine",
            payload={
                "commodity_code": candidate.commodity_code,
                "location_code": candidate.location_code,
                "buy_price_vnd": decision.buy_price_vnd,
                "sell_price_vnd": decision.sell_price_vnd,
                "retail_price_vnd": decision.retail_price_vnd,
                "validation_notes": decision.validation_notes,
            },
        )
        session.add(alert)
        return "FLAGGED"

    return "VERIFIED"

