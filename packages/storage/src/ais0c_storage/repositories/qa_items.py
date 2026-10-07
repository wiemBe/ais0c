"""`qa_items`: cases waiting for an operator's check (architecture §9, "Zorunlu operatör
kontrolü"; decision T-42).

`list_qa_queue` is the queue the analyst API pages through (T-028); `list_qa_items` is what the
case workflow reads for one case.
"""

import uuid
from collections.abc import Collection, Iterable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import QAReason
from ais0c_storage.enums import QAStatus
from ais0c_storage.models import QAItemRow
from ais0c_storage.repositories._common import fetch_all, get_row, insert_row


async def add_qa_items(
    session: AsyncSession, case_id: str, evaluation_no: int, reasons: Iterable[QAReason]
) -> list[QAItemRow]:
    """Open one item per reason and evaluation, in order; a repeated reason opens one item."""
    return [
        await insert_row(
            session,
            QAItemRow,
            dict(
                case_id=case_id,
                evaluation_no=evaluation_no,
                reason=reason,
                status=QAStatus.OPEN,
            ),
        )
        for reason in dict.fromkeys(QAReason(reason) for reason in reasons)
    ]


async def list_qa_items(
    session: AsyncSession,
    case_id: str,
    *,
    evaluation_no: int | None = None,
    statuses: Iterable[QAStatus] | None = None,
) -> list[QAItemRow]:
    """The case's items, oldest first (UUIDv7 IDs sort by the millisecond they were made in)."""
    statement = select(QAItemRow).where(QAItemRow.case_id == case_id)
    if evaluation_no is not None:
        statement = statement.where(QAItemRow.evaluation_no == evaluation_no)
    if statuses is not None:
        statement = statement.where(QAItemRow.status.in_(list(statuses)))
    return await fetch_all(session, statement.order_by(QAItemRow.id))


async def get_qa_item(session: AsyncSession, item_id: uuid.UUID) -> QAItemRow | None:
    return await get_row(session, QAItemRow, item_id)


async def resolve_qa_item(
    session: AsyncSession, item_id: uuid.UUID, *, resolved_by: str, resolved_at: datetime
) -> QAItemRow | None:
    """Mark an open item `resolved` (`POST /qa/{id}/resolve` of the analyst API, T-028).

    `resolved_by` is the operator's OIDC subject. Only an `open` item changes, in one statement,
    so of two operators resolving the same item at once exactly one gets the row back. None when
    there is no such item or it is no longer open; the caller tells the two apart with
    `get_qa_item`.
    """
    statement = (
        update(QAItemRow)
        .where(QAItemRow.id == item_id, QAItemRow.status == QAStatus.OPEN)
        .values(
            status=QAStatus.RESOLVED,
            resolved_by=resolved_by,
            resolved_at=resolved_at,
        )
        .returning(QAItemRow)
    )
    result = await session.scalars(statement, execution_options={"populate_existing": True})
    return result.one_or_none()


async def list_qa_queue(
    session: AsyncSession,
    *,
    statuses: Collection[QAStatus] | None = None,
    reasons: Collection[QAReason] | None = None,
    after: uuid.UUID | None = None,
    limit: int = 50,
) -> list[QAItemRow]:
    """The QA queue across all cases, oldest first (UUIDv7 IDs sort by creation time).

    `after` is the last ID of the previous page, so a page starts after it and a new item never
    repeats or skips one.
    """
    statement = select(QAItemRow)
    if statuses is not None:
        statement = statement.where(QAItemRow.status.in_(list(statuses)))
    if reasons is not None:
        statement = statement.where(QAItemRow.reason.in_(list(reasons)))
    if after is not None:
        statement = statement.where(QAItemRow.id > after)
    return await fetch_all(session, statement.order_by(QAItemRow.id).limit(limit))
