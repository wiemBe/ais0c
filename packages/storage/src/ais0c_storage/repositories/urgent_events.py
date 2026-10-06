"""`urgent_events`: the events an operator should look at first, per evaluation (architecture
§9, "Acil bakılması gereken event'ler")."""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import UrgentEvent
from ais0c_storage.columns import revalidate
from ais0c_storage.models import UrgentEventRow
from ais0c_storage.repositories._common import fetch_all, insert_row


async def replace_urgent_events(
    session: AsyncSession, case_id: str, evaluation_no: int, events: Sequence[UrgentEvent]
) -> list[UrgentEventRow]:
    """Store the evaluation's urgent events in place of any it has, so storing them twice
    leaves one set. Each row's `rank` is its event's."""
    checked = [revalidate(UrgentEvent, event) for event in events]
    await session.execute(
        delete(UrgentEventRow).where(
            UrgentEventRow.case_id == case_id, UrgentEventRow.evaluation_no == evaluation_no
        )
    )
    return [
        await insert_row(
            session,
            UrgentEventRow,
            dict(case_id=case_id, evaluation_no=evaluation_no, rank=event.rank, event=event),
        )
        for event in checked
    ]


async def list_urgent_events(
    session: AsyncSession, case_id: str, *, evaluation_no: int | None = None
) -> list[UrgentEventRow]:
    """The case's urgent events by evaluation and rank; one evaluation's with `evaluation_no`."""
    statement = select(UrgentEventRow).where(UrgentEventRow.case_id == case_id)
    if evaluation_no is not None:
        statement = statement.where(UrgentEventRow.evaluation_no == evaluation_no)
    statement = statement.order_by(
        UrgentEventRow.evaluation_no, UrgentEventRow.rank, UrgentEventRow.id
    )
    return await fetch_all(session, statement)
