"""Writing a note to QRadar once (architecture §9, "QRadar offense notu"; T-23).

Adding a note is not idempotent: a retried activity could add the same note twice. The writer
therefore:

1. builds the note's text; nothing else happens if it cannot be built (`InvalidNote`);
2. returns at once if `notes_written` already records the note as written or as a duplicate;
3. checks the kill switch, so a platform with writes off (shadow mode) does not even read
   QRadar;
4. reads the offense's notes and skips the write if one of them has the note's run marker on
   its first line: an earlier attempt wrote it, but did not get to record it;
5. checks the kill switch again right before the write, then adds the note.

Each attempt's outcome goes to `notes_written`, keyed by offense and run marker; a later
attempt updates the row. A note the kill switch held back is recorded as `disabled`, without an
error text: it is not a failure (T-37). Only `written` and `skipped_duplicate` end the work, so
a later attempt with writes on writes a `disabled` or `failed` note. A written note is also
appended to `audit_log` as `note.write`.

QRadar is reached through an `OffenseNotes` (the gateway's `qradar-note-write` profile,
`ais0c_activities.note`). The writer opens it only for steps 4 and 5.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_executor.common import KillSwitch, WritesDisabled, clean_text
from ais0c_executor.note.errors import OffenseNotesError
from ais0c_executor.note.render import note_run_marker, render_note
from ais0c_executor.note.request import (
    NoteKind,
    NoteOutcome,
    NoteRequest,
    NoteResult,
    validated,
)
from ais0c_storage import ActorKind, NoteStatus
from ais0c_storage.models import NoteWrittenRow
from ais0c_storage.repositories import append_audit, get_note, update_note_status

# Who writes notes, in audit_log and agent_runs.
EXECUTOR_ID: Final = "action-executor"
NOTE_AUDIT_ACTION: Final = "note.write"
NOTE_OBJECT_TYPE: Final = "offense"
MAX_ERROR_LENGTH: Final = 500

_STATUS: Final = {
    NoteResult.WRITTEN: NoteStatus.WRITTEN,
    NoteResult.SKIPPED_DUPLICATE: NoteStatus.SKIPPED_DUPLICATE,
    NoteResult.WRITES_DISABLED: NoteStatus.DISABLED,
    NoteResult.FAILED: NoteStatus.FAILED,
}
_DONE: Final = {
    NoteStatus.WRITTEN: NoteResult.WRITTEN,
    NoteStatus.SKIPPED_DUPLICATE: NoteResult.SKIPPED_DUPLICATE,
}


class OffenseNotes(Protocol):
    """QRadar's notes of offenses. Raises `OffenseNotesError` when a call does not succeed."""

    async def notes_containing(self, offense_id: int, text: str) -> Sequence[str]:
        """The texts of the offense's notes that hold `text`; it may return more notes."""
        ...

    async def add_note(self, offense_id: int, text: str) -> None: ...


type OpenNotes = Callable[[], AbstractAsyncContextManager[OffenseNotes]]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Note:
    request: NoteRequest
    kind: NoteKind
    text: str

    @property
    def offense_id(self) -> int:
        return self.request.offense_id

    @property
    def run_marker(self) -> str:
        return self.request.run_marker


class NoteWriter:
    """Writes notes and records them through `sessions`."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        kill_switch: KillSwitch | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._kill_switch = KillSwitch(sessions) if kill_switch is None else kill_switch
        self._clock = clock

    async def write(self, request: NoteRequest, open_notes: OpenNotes) -> NoteOutcome:
        """Write the note of `request` unless it is already in QRadar or writes are off.

        Raises `InvalidNote` when the note cannot be built, and `OffenseNotesError` when QRadar
        could not be read or written and a later attempt may succeed; that attempt is recorded
        as `failed` first. A refused call is recorded and returned as `failed`.
        """
        request = validated(request)
        note = _Note(request=request, kind=request.note_kind, text=render_note(request))
        done = await self._recorded(note)
        if done is not None:
            return done
        try:
            await self._kill_switch.check()
        except WritesDisabled as error:
            return await self._finish(note, NoteResult.WRITES_DISABLED, str(error))
        try:
            async with open_notes() as qradar:
                result, error = await self._write(note, qradar)
        except OffenseNotesError as failure:
            outcome = await self._finish(note, NoteResult.FAILED, str(failure))
            if failure.retryable:
                raise
            return outcome
        return await self._finish(note, result, error)

    async def _write(self, note: _Note, qradar: OffenseNotes) -> tuple[NoteResult, str | None]:
        texts = await qradar.notes_containing(note.offense_id, f"run:{note.run_marker}")
        if any(note_run_marker(text) == note.run_marker for text in texts):
            return NoteResult.SKIPPED_DUPLICATE, None
        try:
            await self._kill_switch.guarded(lambda: qradar.add_note(note.offense_id, note.text))
        except WritesDisabled as error:
            return NoteResult.WRITES_DISABLED, str(error)
        return NoteResult.WRITTEN, None

    async def _recorded(self, note: _Note) -> NoteOutcome | None:
        """The outcome if the note is already recorded as written or as a duplicate."""
        async with self._sessions() as session:
            row = await get_note(session, offense_id=note.offense_id, run_marker=note.run_marker)
        result = None if row is None else _DONE.get(row.status)
        if result is None:
            return None
        return NoteOutcome(result=result, offense_id=note.offense_id, run_marker=note.run_marker)

    async def _finish(self, note: _Note, result: NoteResult, error: str | None) -> NoteOutcome:
        """Record the attempt and return its outcome. The outcome says why a note was not
        written; the record keeps that text only for a failure."""
        message = None if error is None else clean_text(error, MAX_ERROR_LENGTH)
        status = _STATUS[result]
        await self._record(note, status, message if status is NoteStatus.FAILED else None)
        return NoteOutcome(
            result=result, offense_id=note.offense_id, run_marker=note.run_marker, error=message
        )

    async def _record(self, note: _Note, status: NoteStatus, error: str | None) -> None:
        at = self._clock()
        async with self._sessions.begin() as session:
            await _save_note(session, note, status=status, error=error, at=at)
            if status is NoteStatus.WRITTEN:
                await append_audit(
                    session,
                    actor_kind=ActorKind.SYSTEM,
                    actor_id=EXECUTOR_ID,
                    action=NOTE_AUDIT_ACTION,
                    object_type=NOTE_OBJECT_TYPE,
                    object_id=str(note.offense_id),
                    details={
                        "case_id": note.request.case_id,
                        "evaluation_no": note.request.evaluation_no,
                        "run_marker": note.run_marker,
                        "kind": note.kind.value,
                    },
                )


async def _save_note(
    session: AsyncSession, note: _Note, *, status: NoteStatus, error: str | None, at: datetime
) -> None:
    """Insert the note's `notes_written` row, or update it if an earlier attempt made it.

    `record_note` takes a `NoteContent`, which a no-decision note does not have, so the row is
    inserted here, in a savepoint: if a concurrent attempt inserted it first, the row is
    updated instead.
    """
    offense_id, run_marker = note.offense_id, note.run_marker
    if await get_note(session, offense_id=offense_id, run_marker=run_marker) is None:
        statement = insert(NoteWrittenRow).values(
            case_id=note.request.case_id,
            offense_id=offense_id,
            evaluation_no=note.request.evaluation_no,
            run_marker=run_marker,
            status=status,
            error=error,
            written_at=at,
        )
        try:
            async with session.begin_nested():
                await session.execute(statement)
            return
        except IntegrityError:
            if await get_note(session, offense_id=offense_id, run_marker=run_marker) is None:
                raise
    await update_note_status(
        session,
        offense_id=offense_id,
        run_marker=run_marker,
        status=status,
        written_at=at,
        error=error,
    )
