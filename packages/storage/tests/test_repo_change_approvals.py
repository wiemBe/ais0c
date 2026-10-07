"""Repository functions of `change_approvals` (T-033 criterion 1; D-36, T-77)."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.enums import ChangeObjectType, ChangeRejectReason, ChangeStatus
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.models import ChangeApprovalRow
from ais0c_storage.repositories import (
    decide_change,
    get_change,
    get_pending_change,
    list_changes,
    request_change,
)

pytestmark = pytest.mark.anyio

RULE = ChangeObjectType.CATALOG_RULE


async def ask(
    session: AsyncSession, object_id: str = "100201", requested_by: str = "admin-a"
) -> ChangeApprovalRow:
    return await request_change(
        session,
        object_type=RULE,
        object_id=object_id,
        object_version="v1",
        change={"action": "update", "before": {"mode": "normal"}, "after": {"mode": "ignore"}},
        requested_by=requested_by,
    )


async def test_a_request_is_written_pending(session: AsyncSession) -> None:
    row = await ask(session)

    assert row.status is ChangeStatus.PENDING
    assert (row.decided_by, row.decided_at, row.reason) == (None, None, None)
    assert row.requested_at is not None
    assert await get_pending_change(session, RULE, "100201") == row
    assert await get_change(session, row.id) == row


async def test_an_object_has_one_pending_request(session: AsyncSession) -> None:
    await ask(session)

    with pytest.raises(DuplicateError):
        await ask(session, requested_by="admin-b")
    # Another object, or another kind of object with the same ID, is free.
    await ask(session, "100202")
    await request_change(
        session,
        object_type=ChangeObjectType.CATALOG_LOG_SOURCE,
        object_id="100201",
        object_version="v1",
        change={},
        requested_by="admin-a",
    )


async def test_a_decided_request_frees_the_object(session: AsyncSession) -> None:
    first = await ask(session)
    await decide_change(session, first.id, status=ChangeStatus.APPROVED, decided_by="admin-b")

    second = await ask(session)

    assert second.id != first.id
    assert (await get_pending_change(session, RULE, "100201")) == second


async def test_approving_records_the_decider_and_the_time(session: AsyncSession) -> None:
    row = await ask(session)

    decided = await decide_change(
        session, row.id, status=ChangeStatus.APPROVED, decided_by="admin-b"
    )

    assert (decided.status, decided.decided_by) == (ChangeStatus.APPROVED, "admin-b")
    assert decided.decided_at is not None
    assert decided.reason is None
    assert await get_pending_change(session, RULE, "100201") is None


async def test_a_rejection_keeps_its_reason_and_comment(session: AsyncSession) -> None:
    row = await ask(session)

    decided = await decide_change(
        session,
        row.id,
        status=ChangeStatus.REJECTED,
        reason=ChangeRejectReason.REJECTED_BY_ADMIN,
        decided_by="admin-b",
        comment="Wrong rule.",
    )

    assert (decided.reason, decided.comment) == (
        ChangeRejectReason.REJECTED_BY_ADMIN,
        "Wrong rule.",
    )


async def test_the_database_refuses_the_requester_as_decider(session: AsyncSession) -> None:
    row = await ask(session)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await decide_change(session, row.id, status=ChangeStatus.APPROVED, decided_by="admin-a")
    assert (await get_change(session, row.id)).status is ChangeStatus.PENDING  # type: ignore[union-attr]


async def test_a_decided_request_cannot_be_decided_again(session: AsyncSession) -> None:
    row = await ask(session)
    await decide_change(
        session,
        row.id,
        status=ChangeStatus.REJECTED,
        reason=ChangeRejectReason.WITHDRAWN,
    )

    with pytest.raises(NotFoundError):
        await decide_change(session, row.id, status=ChangeStatus.APPROVED, decided_by="admin-b")
    with pytest.raises(NotFoundError):
        await decide_change(
            session, uuid.uuid4(), status=ChangeStatus.APPROVED, decided_by="admin-b"
        )
    with pytest.raises(ValueError, match="approved or rejected"):
        await decide_change(session, row.id, status=ChangeStatus.PENDING)


async def test_requests_are_listed_newest_first_with_filters(session: AsyncSession) -> None:
    first = await ask(session, "1")
    second = await ask(session, "2")
    await decide_change(session, first.id, status=ChangeStatus.APPROVED, decided_by="admin-b")
    third = await request_change(
        session,
        object_type=ChangeObjectType.CRITICAL_ASSET,
        object_id="ip:192.0.2.7",
        object_version="absent",
        change={},
        requested_by="admin-a",
    )

    everything = await list_changes(session)
    assert {row.id for row in everything} == {first.id, second.id, third.id}
    assert everything == sorted(everything, key=lambda r: (r.requested_at, r.id), reverse=True)
    pending = await list_changes(session, status=ChangeStatus.PENDING)
    assert [r.id for r in pending] == [r.id for r in everything if r.id != first.id]
    by_type = await list_changes(session, object_type=ChangeObjectType.CRITICAL_ASSET)
    assert [r.id for r in by_type] == [third.id]
    last = everything[0]
    rest = await list_changes(session, after=(last.requested_at, last.id))
    assert [r.id for r in rest] == [r.id for r in everything[1:]]
    assert len(await list_changes(session, limit=2)) == 2
