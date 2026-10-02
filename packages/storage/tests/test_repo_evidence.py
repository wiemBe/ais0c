"""Repository functions of `evidence` (criterion 6)."""

from datetime import timedelta

import pytest
import storage_payloads as payloads
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import EVIDENCE_ID, T1

from ais0c_contracts import EvidenceSource
from ais0c_storage.errors import DuplicateError
from ais0c_storage.repositories import (
    EXCERPT_RETENTION,
    find_unknown_evidence_ids,
    get_evidence,
    get_evidence_refs,
    record_evidence,
    to_evidence_ref,
)

pytestmark = pytest.mark.anyio


async def test_record_and_read_evidence(session: AsyncSession) -> None:
    ref = payloads.evidence_ref()

    row = await record_evidence(session, ref)

    assert row.expires_at == ref.retrieved_at + EXCERPT_RETENTION == T1 + timedelta(days=30)
    assert row.source is EvidenceSource.QRADAR
    await session.commit()
    session.expunge_all()
    stored = await get_evidence(session, EVIDENCE_ID)
    assert stored is not None
    assert to_evidence_ref(stored) == ref
    assert await get_evidence(session, "ev_unknown") is None


async def test_expiry_can_be_given(session: AsyncSession) -> None:
    expires_at = T1 + timedelta(days=1)

    row = await record_evidence(session, payloads.evidence_ref(), expires_at=expires_at)

    assert row.expires_at == expires_at


async def test_evidence_id_is_recorded_once(session: AsyncSession) -> None:
    await record_evidence(session, payloads.evidence_ref())

    with pytest.raises(DuplicateError):
        await record_evidence(session, payloads.evidence_ref())


async def test_lookup_of_several_ids(session: AsyncSession) -> None:
    for evidence_id in ("ev_1", "ev_2"):
        await record_evidence(session, payloads.evidence_ref(evidence_id))

    refs = await get_evidence_refs(session, ["ev_1", "ev_2", "ev_3"])

    assert sorted(refs) == ["ev_1", "ev_2"]
    assert refs["ev_2"] == payloads.evidence_ref("ev_2")
    assert await find_unknown_evidence_ids(session, ["ev_1", "ev_3", "ev_9"]) == {"ev_3", "ev_9"}
    assert await find_unknown_evidence_ids(session, []) == set()
