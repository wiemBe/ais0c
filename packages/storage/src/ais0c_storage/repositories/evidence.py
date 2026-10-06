"""`evidence`: the `EvidenceRef`s the gateway records."""

from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EvidenceRef
from ais0c_storage.columns import revalidate
from ais0c_storage.models import EvidenceRow
from ais0c_storage.repositories._common import fetch_all, get_row, insert_new

# Excerpts are kept for 30 days (D-08); the evidence itself stays at the source.
EXCERPT_RETENTION = timedelta(days=30)


async def record_evidence(
    session: AsyncSession, ref: EvidenceRef, *, expires_at: datetime | None = None
) -> EvidenceRow:
    """Store `ref`. `expires_at`, when the excerpt is to be emptied, defaults to
    `retrieved_at` + 30 days. Raises `DuplicateError` if the evidence ID exists."""
    ref = revalidate(EvidenceRef, ref)
    values = dict(
        evidence_id=ref.evidence_id,
        source=ref.source,
        query_text=ref.query_text,
        query_hash=ref.query_hash,
        time_start=ref.time_start,
        time_end=ref.time_end,
        identifiers=ref.identifiers,
        excerpt=ref.excerpt,
        retrieved_at=ref.retrieved_at,
        expires_at=ref.retrieved_at + EXCERPT_RETENTION if expires_at is None else expires_at,
    )
    return await insert_new(session, EvidenceRow, values, f"evidence {ref.evidence_id!r}")


async def get_evidence(session: AsyncSession, evidence_id: str) -> EvidenceRow | None:
    return await get_row(session, EvidenceRow, evidence_id)


async def list_evidence_by_ids(
    session: AsyncSession, evidence_ids: Collection[str]
) -> list[EvidenceRow]:
    """The stored evidence among `evidence_ids`, by ID; unknown IDs are left out.

    `get_evidence_refs` gives the same rows as `EvidenceRef` models; this keeps the row, so a
    caller can show `retrieved_at` and `expires_at` as they are (the analyst API's evidence list,
    T-028).
    """
    if not evidence_ids:
        return []
    statement = (
        select(EvidenceRow)
        .where(EvidenceRow.evidence_id.in_(list(evidence_ids)))
        .order_by(EvidenceRow.retrieved_at, EvidenceRow.evidence_id)
    )
    return await fetch_all(session, statement)


async def get_evidence_refs(
    session: AsyncSession, evidence_ids: Collection[str]
) -> dict[str, EvidenceRef]:
    """The stored evidence among `evidence_ids`, by ID; unknown IDs are left out."""
    statement = select(EvidenceRow).where(EvidenceRow.evidence_id.in_(list(evidence_ids)))
    return {row.evidence_id: to_evidence_ref(row) for row in await fetch_all(session, statement)}


async def find_unknown_evidence_ids(
    session: AsyncSession, evidence_ids: Collection[str]
) -> set[str]:
    """IDs the gateway never recorded. A claim citing one of them is rejected (contracts.md)."""
    wanted = set(evidence_ids)
    statement = select(EvidenceRow.evidence_id).where(EvidenceRow.evidence_id.in_(list(wanted)))
    return wanted - set(await session.scalars(statement))


def to_evidence_ref(row: EvidenceRow) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=row.evidence_id,
        source=row.source,
        query_hash=row.query_hash,
        query_text=row.query_text,
        time_start=row.time_start,
        time_end=row.time_end,
        identifiers=row.identifiers,
        excerpt=row.excerpt,
        retrieved_at=row.retrieved_at,
    )
