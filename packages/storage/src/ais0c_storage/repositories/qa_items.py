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
