"""`qa_items`: cases waiting for an operator's check (architecture §9, "Zorunlu operatör
kontrolü"; decision T-42)."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import QAReason
from ais0c_storage.enums import QAStatus
from ais0c_storage.models import QAItemRow
from ais0c_storage.repositories._common import fetch_all, insert_row


async def add_qa_items(
    session: AsyncSession, case_id: str, reasons: Iterable[QAReason]
) -> list[QAItemRow]:
    """Open one item per reason, in the order given; a reason given twice opens one item.

    The table has no evaluation number, so it cannot tell a retry from the next evaluation:
    the caller writes an evaluation's items once, in the transaction that records its decision.
    """
    return [
        await insert_row(
            session, QAItemRow, dict(case_id=case_id, reason=reason, status=QAStatus.OPEN)
        )
        for reason in dict.fromkeys(QAReason(reason) for reason in reasons)
    ]


async def list_qa_items(
    session: AsyncSession, case_id: str, *, statuses: Iterable[QAStatus] | None = None
) -> list[QAItemRow]:
    """The case's items, oldest first (UUIDv7 IDs sort by the millisecond they were made in)."""
    statement = select(QAItemRow).where(QAItemRow.case_id == case_id)
    if statuses is not None:
        statement = statement.where(QAItemRow.status.in_(list(statuses)))
    return await fetch_all(session, statement.order_by(QAItemRow.id))
