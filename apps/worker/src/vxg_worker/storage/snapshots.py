"""Gzip-compressed raw HTTP/Playwright response archiving & retention cleanup."""

from __future__ import annotations

import gzip
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vxg_shared.db.models import PriceRecord, RawEvidenceSnapshot
from vxg_shared.enums import VerificationStatus
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import to_vn_tz, vn_now

_PROTECTED_VERIFIED_STATUSES: tuple[str, ...] = (
    VerificationStatus.VERIFIED.value,
    VerificationStatus.CROSS_CHECKED.value,
    VerificationStatus.OVERRIDDEN_MANUAL.value,
)


@dataclass(slots=True)
class SnapshotPruneResult:
    """Summary of expired snapshot payload cleanup."""

    retention_days: int
    cutoff_vn: datetime
    deleted_count: int
    freed_bytes: int


async def store_raw_snapshot(
    session: AsyncSession,
    *,
    raw_bytes: bytes,
    content_type: str,
) -> tuple[uuid.UUID, str]:
    """Compress and upsert raw evidence payload keyed by deterministic SHA-256."""
    content_sha256 = compute_content_sha256(raw_bytes)
    compressed = gzip.compress(raw_bytes, compresslevel=6)
    now_vn = vn_now()
    normalized_ct = (content_type or "application/octet-stream").split(";")[0].strip()[:64]

    stmt = (
        pg_insert(RawEvidenceSnapshot)
        .values(
            content_sha256=content_sha256,
            content_type=normalized_ct or "application/octet-stream",
            byte_size=len(raw_bytes),
            compressed_payload=compressed,
            first_seen_at=now_vn,
            last_seen_at=now_vn,
        )
        .on_conflict_do_update(
            index_elements=["content_sha256"],
            set_={"last_seen_at": now_vn},
        )
        .returning(RawEvidenceSnapshot.id)
    )
    snapshot_id = (await session.execute(stmt)).scalar_one()
    return snapshot_id, content_sha256


async def prune_expired_snapshots(
    session: AsyncSession,
    *,
    retention_days: int = 90,
    now: datetime | None = None,
) -> SnapshotPruneResult:
    """Delete expired gzip snapshots while preserving history and latest verified proofs."""
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")

    reference_vn = to_vn_tz(now) if now is not None else vn_now()
    cutoff_vn = reference_vn - timedelta(days=retention_days)

    protected_shas_subquery = (
        select(PriceRecord.evidence_sha256)
        .where(PriceRecord.verification_status.in_(_PROTECTED_VERIFIED_STATUSES))
        .distinct(PriceRecord.commodity_id, PriceRecord.location_id, PriceRecord.source_id)
        .order_by(
            PriceRecord.commodity_id.asc(),
            PriceRecord.location_id.asc(),
            PriceRecord.source_id.asc(),
            PriceRecord.effective_date.desc(),
            func.coalesce(PriceRecord.source_published_at, PriceRecord.crawled_at).desc(),
            PriceRecord.crawled_at.desc(),
        )
    )

    stmt = (
        delete(RawEvidenceSnapshot)
        .where(
            RawEvidenceSnapshot.last_seen_at < cutoff_vn,
            RawEvidenceSnapshot.content_sha256.notin_(protected_shas_subquery),
        )
        .returning(RawEvidenceSnapshot.byte_size)
    )
    deleted_sizes = list((await session.execute(stmt)).scalars().all())
    return SnapshotPruneResult(
        retention_days=retention_days,
        cutoff_vn=cutoff_vn,
        deleted_count=len(deleted_sizes),
        freed_bytes=sum(int(sz) for sz in deleted_sizes),
    )


def decompress_snapshot(compressed_payload: bytes) -> bytes:
    """Decompress stored gzip evidence bytes for verification or debugging."""
    return gzip.decompress(compressed_payload)
