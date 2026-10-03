"""`notes_written`: QRadar notes the executor wrote or skipped (architecture §9).

(`offense_id`, `run_marker`) is unique, so a retried activity cannot record the same note twice.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import NoteContent
from ais0c_storage.columns import revalidate
from ais0c_storage.enums import NoteStatus
from ais0c_storage.models import NoteWrittenRow
from ais0c_storage.repositories._common import fetch_all, fetch_one, insert_new, update_one


async def record_note(
    session: AsyncSession,
    note: NoteContent,
    *,
    case_id: str,
    status: NoteStatus,
    written_at: datetime,
    error: str | None = None,
) -> NoteWrittenRow:
    """Record the outcome of writing `note`.

    Raises `DuplicateError` if a note with the same offense and run marker is recorded.
    """
    note = revalidate(NoteContent, note)
    values = dict(
        case_id=case_id,
        offense_id=note.offense_id,
        evaluation_no=note.evaluation_no,
        run_marker=note.run_marker,
        status=status,
        error=error,
        written_at=written_at,
    )
    description = f"note run:{note.run_marker} on offense {note.offense_id}"
    return await insert_new(session, NoteWrittenRow, values, description)


async def get_note(
    session: AsyncSession, *, offense_id: int, run_marker: str
) -> NoteWrittenRow | None:
    statement = select(NoteWrittenRow).where(
        NoteWrittenRow.offense_id == offense_id, NoteWrittenRow.run_marker == run_marker
    )
    return await fetch_one(session, statement)


async def update_note_status(
    session: AsyncSession,
    *,
    offense_id: int,
    run_marker: str,
    status: NoteStatus,
    written_at: datetime,
    error: str | None = None,
) -> NoteWrittenRow:
    """For example `failed` -> `written` when a retry succeeds. `error` replaces the old one."""
    statement = (
        update(NoteWrittenRow)
        .where(NoteWrittenRow.offense_id == offense_id, NoteWrittenRow.run_marker == run_marker)
        .values(status=status, written_at=written_at, error=error)
    )
    description = f"note run:{run_marker} on offense {offense_id}"
    return await update_one(session, statement, NoteWrittenRow, description)


async def list_notes(session: AsyncSession, case_id: str) -> list[NoteWrittenRow]:
    """Notes of a case, by evaluation."""
    statement = (
        select(NoteWrittenRow)
        .where(NoteWrittenRow.case_id == case_id)
        .order_by(NoteWrittenRow.evaluation_no, NoteWrittenRow.written_at, NoteWrittenRow.id)
    )
    return await fetch_all(session, statement)
