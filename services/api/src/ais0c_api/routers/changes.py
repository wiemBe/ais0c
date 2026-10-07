"""Double control: the requests that wait for a second admin (api.md "Değişiklik onayları";
T-033, D-36, T-77).

Every endpoint is an admin's. A request is made by the endpoint that changes the object (the
catalog, the critical assets, the kill switch); here a second admin reads it, approves or rejects
it, and the requester may take it back. The requester can never decide their own request: 403
(`change.self_approval`), and the database refuses it too.

Approving writes the change, the request's end and both audit rows in one transaction. An object
that changed after the request was made makes the approval a 409 (`change.stale`); that answer is
returned rather than raised so the request's end, `rejected` with reason `stale`, is committed.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_api import audit
from ais0c_api.auth import Session
from ais0c_api.changes import Stale, apply_change, item, mark_stale, stale_response
from ais0c_api.dependencies import ADMIN, ReadSession, WriteSession
from ais0c_api.models import ChangeItem, ChangeReject, Page
from ais0c_api.pagination import (
    DEFAULT_LIMIT,
    CursorParam,
    LimitParam,
    paginate,
    timestamp_cursor,
)
from ais0c_api.problems import Problem, invalid_cursor, not_found
from ais0c_storage.enums import ChangeObjectType, ChangeRejectReason, ChangeStatus
from ais0c_storage.models import ChangeApprovalRow
from ais0c_storage.repositories import decide_change, get_change, list_changes

router = APIRouter(prefix="/changes", tags=["changes"])

StatusParam = Annotated[ChangeStatus | None, Query()]
ObjectTypeParam = Annotated[ChangeObjectType | None, Query()]


def _details(row: ChangeApprovalRow) -> dict[str, JsonValue]:
    return {
        "object_type": row.object_type.value,
        "object_id": row.object_id,
        "requested_by": row.requested_by,
    }


async def _pending(session: AsyncSession, change_id: uuid.UUID) -> ChangeApprovalRow:
    """The request, locked; a request that was decided is a 409."""
    row = await get_change(session, change_id, lock=True)
    if row is None:
        raise not_found("change.not_found")
    if row.status is not ChangeStatus.PENDING:
        raise Problem(409, "change.already_decided", detail="the change was already decided")
    return row


def _not_self(row: ChangeApprovalRow, user: Session) -> None:
    if row.requested_by == user.subject:
        raise Problem(
            403, "change.self_approval", detail="the admin who asked cannot decide the request"
        )


@router.get("", response_model=Page[ChangeItem])
async def get_changes(
    _user: ADMIN,
    session: ReadSession,
    status: StatusParam = None,
    object_type: ObjectTypeParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = DEFAULT_LIMIT,
) -> Page[ChangeItem]:
    """The requests, newest first; `status` and `object_type` narrow the list."""
    key = timestamp_cursor(cursor, field="requested_at")
    after = None
    if key is not None:
        try:
            after = (key[0], uuid.UUID(key[1]))
        except ValueError:
            raise invalid_cursor() from None
    rows = await list_changes(
        session, status=status, object_type=object_type, after=after, limit=limit + 1
    )
    page, next_cursor = paginate(
        rows, limit, lambda row: [row.requested_at.isoformat(), str(row.id)]
    )
    return Page(items=[item(row) for row in page], next_cursor=next_cursor)


@router.get("/{change_id}", response_model=ChangeItem)
async def get_one_change(change_id: uuid.UUID, _user: ADMIN, session: ReadSession) -> ChangeItem:
    """One request; an unknown ID is a 404."""
    row = await get_change(session, change_id)
    if row is None:
        raise not_found("change.not_found")
    return item(row)


@router.post("/{change_id}/approve", response_model=ChangeItem)
async def post_approve(
    change_id: uuid.UUID, user: ADMIN, session: WriteSession
) -> ChangeItem | JSONResponse:
    """Approve and apply a request (an admin other than the requester).

    The change, the request's end and the audit rows are one transaction. A decided request is a
    409 (`change.already_decided`), the requester's own request a 403 (`change.self_approval`), and
    an object that changed since the request a 409 (`change.stale`) that ends the request.
    """
    row = await _pending(session, change_id)
    _not_self(row, user)
    try:
        await apply_change(session, row, user)
    except Stale:
        await mark_stale(session, row, actor_id=user.subject, cause="object_changed")
        return stale_response()
    decided = await decide_change(
        session, change_id, status=ChangeStatus.APPROVED, decided_by=user.subject
    )
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CHANGE_APPROVE,
        object_type=audit.OBJECT_CHANGE,
        object_id=str(change_id),
        details=_details(row),
    )
    return item(decided)


@router.post("/{change_id}/reject", response_model=ChangeItem)
async def post_reject(
    change_id: uuid.UUID, body: ChangeReject, user: ADMIN, session: WriteSession
) -> ChangeItem:
    """Reject a request (an admin other than the requester); nothing changes."""
    row = await _pending(session, change_id)
    _not_self(row, user)
    comment = (body.comment or "").strip() or None
    decided = await decide_change(
        session,
        change_id,
        status=ChangeStatus.REJECTED,
        reason=ChangeRejectReason.REJECTED_BY_ADMIN,
        decided_by=user.subject,
        comment=comment,
    )
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CHANGE_REJECT,
        object_type=audit.OBJECT_CHANGE,
        object_id=str(change_id),
        details={**_details(row), "comment": comment},
    )
    return item(decided)


@router.post("/{change_id}/withdraw", response_model=ChangeItem)
async def post_withdraw(change_id: uuid.UUID, user: ADMIN, session: WriteSession) -> ChangeItem:
    """Take a request back (only the admin who asked); nothing changes and nobody decided it."""
    row = await _pending(session, change_id)
    if row.requested_by != user.subject:
        raise Problem(
            403, "change.not_requester", detail="only the admin who asked can withdraw the request"
        )
    ended = await decide_change(
        session, change_id, status=ChangeStatus.REJECTED, reason=ChangeRejectReason.WITHDRAWN
    )
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CHANGE_WITHDRAW,
        object_type=audit.OBJECT_CHANGE,
        object_id=str(change_id),
        details=_details(row),
    )
    return item(ended)
