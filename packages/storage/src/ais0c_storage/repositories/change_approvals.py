"""`change_approvals`: changes that wait for a second admin (D-36, T-77).

A request is written `pending` and ends once: `approved` or `rejected` (`rejected_by_admin`,
`stale`, `withdrawn`). The database holds the rules (`decided_by` is not the requester, one pending
request per object); this module only moves a pending request to its end, and it never applies the
change itself: the API does, in the same transaction, so the change and its decision are one.
"""

import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.enums import ChangeObjectType, ChangeRejectReason, ChangeStatus
from ais0c_storage.models import ChangeApprovalRow
from ais0c_storage.repositories._common import fetch_all, fetch_one, insert_new, update_one


async def request_change(
    session: AsyncSession,
    *,
    object_type: ChangeObjectType,
    object_id: str,
    object_version: str,
    change: JsonValue,
    requested_by: str,
) -> ChangeApprovalRow:
    """Write a `pending` request. Raises `DuplicateError` if the object already has one."""
    values = dict(
        object_type=ChangeObjectType(object_type),
        object_id=object_id,
        object_version=object_version,
        change=change,
        requested_by=requested_by,
        status=ChangeStatus.PENDING,
    )
    return await insert_new(
        session, ChangeApprovalRow, values, f"a pending change of {object_type} {object_id}"
    )


async def get_change(
    session: AsyncSession, change_id: uuid.UUID, *, lock: bool = False
) -> ChangeApprovalRow | None:
    """The request as it is now; `lock` holds its row until the transaction ends, so two
    decisions of one request take turns."""
    statement = select(ChangeApprovalRow).where(ChangeApprovalRow.id == change_id)
    if lock:
        statement = statement.with_for_update()
    return await fetch_one(session, statement)


async def get_pending_change(
    session: AsyncSession, object_type: ChangeObjectType, object_id: str, *, lock: bool = False
) -> ChangeApprovalRow | None:
    """The object's pending request, if any."""
    statement = select(ChangeApprovalRow).where(
        ChangeApprovalRow.object_type == ChangeObjectType(object_type),
        ChangeApprovalRow.object_id == object_id,
        ChangeApprovalRow.status == ChangeStatus.PENDING,
    )
    if lock:
        statement = statement.with_for_update()
    return await fetch_one(session, statement)


async def list_changes(
    session: AsyncSession,
    *,
    status: ChangeStatus | None = None,
    object_type: ChangeObjectType | None = None,
    after: tuple[datetime, uuid.UUID] | None = None,
    limit: int = 50,
) -> list[ChangeApprovalRow]:
    """Requests, newest first; `after` is the (requested_at, id) of the last row of the page."""
    statement = select(ChangeApprovalRow)
    if status is not None:
        statement = statement.where(ChangeApprovalRow.status == ChangeStatus(status))
    if object_type is not None:
        statement = statement.where(ChangeApprovalRow.object_type == ChangeObjectType(object_type))
    if after is not None:
        statement = statement.where(
            tuple_(ChangeApprovalRow.requested_at, ChangeApprovalRow.id) < tuple_(*after)
        )
    statement = statement.order_by(
        ChangeApprovalRow.requested_at.desc(), ChangeApprovalRow.id.desc()
    ).limit(limit)
    return await fetch_all(session, statement)


async def decide_change(
    session: AsyncSession,
    change_id: uuid.UUID,
    *,
    status: ChangeStatus,
    reason: ChangeRejectReason | None = None,
    decided_by: str | None = None,
    comment: str | None = None,
) -> ChangeApprovalRow:
    """End a `pending` request; `decided_at` is the database's transaction time.

    `decided_by` is the second admin of an approval or a rejection and empty for a withdrawn or
    stale request; the database refuses the requester as `decided_by`. Raises `NotFoundError`
    when there is no pending request with this ID.
    """
    status = ChangeStatus(status)
    if status is ChangeStatus.PENDING:
        raise ValueError("a request is decided as approved or rejected")
    statement = (
        update(ChangeApprovalRow)
        .where(ChangeApprovalRow.id == change_id, ChangeApprovalRow.status == ChangeStatus.PENDING)
        .values(
            status=status,
            reason=None if reason is None else ChangeRejectReason(reason),
            decided_by=decided_by,
            decided_at=func.now(),
            comment=comment,
        )
    )
    return await update_one(session, statement, ChangeApprovalRow, f"pending change {change_id}")
