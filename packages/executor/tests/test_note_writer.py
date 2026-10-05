"""Writing a note once (T-019 criteria 3-5): the duplicate guard, the kill switch and the
`notes_written` record, on a real database. A note the kill switch held back is `disabled`
(T-041 criterion 4). QRadar is a fake `OffenseNotes` that keeps notes in
memory and, like QRadar's `note_text LIKE` filter, returns the notes that hold a text."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta

import pytest
from note_payloads import (
    CASE_ID,
    GROUP_CASE_ID,
    GROUP_ID,
    MARKER,
    OFFENSE_ID,
    T0,
    evaluation_note,
    group_note,
    no_decision_note,
)
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import CaseSource
from ais0c_executor.note import (
    InvalidNote,
    NoteOutcome,
    NoteRequest,
    NoteResult,
    NoteWriter,
    OffenseNotes,
    OffenseNotesError,
    note_run_marker,
    render_note,
)
from ais0c_storage import ActorKind, NoteStatus, PlatformFlag
from ais0c_storage.models import AuditLogRow, NoteWrittenRow
from ais0c_storage.repositories import create_case, get_note, list_audit, set_platform_flag

pytestmark = pytest.mark.anyio

type Sessions = async_sessionmaker[AsyncSession]

NOW = T0 + timedelta(minutes=1)


class FakeQRadar:
    """The notes of offenses; failures can be queued for the next reads and writes."""

    def __init__(self) -> None:
        self.notes: dict[int, list[str]] = {}
        self.reads: list[tuple[int, str]] = []
        self.adds: list[tuple[int, str]] = []
        self.opened = 0
        self.read_failures: list[OffenseNotesError] = []
        # (whether QRadar keeps the note before the error, the error)
        self.add_failures: list[tuple[bool, OffenseNotesError]] = []
        self.after_read: Callable[[], Awaitable[None]] | None = None

    async def notes_containing(self, offense_id: int, text: str) -> list[str]:
        self.reads.append((offense_id, text))
        if self.read_failures:
            raise self.read_failures.pop(0)
        found = [note for note in self.notes.get(offense_id, []) if text in note]
        if self.after_read is not None:
            await self.after_read()
        return found

    async def add_note(self, offense_id: int, text: str) -> None:
        self.adds.append((offense_id, text))
        if self.add_failures:
            kept, error = self.add_failures.pop(0)
            if kept:
                self.notes.setdefault(offense_id, []).append(text)
            raise error
        self.notes.setdefault(offense_id, []).append(text)

    def open(self) -> AbstractAsyncContextManager[OffenseNotes]:
        @asynccontextmanager
        async def opened() -> AsyncIterator[OffenseNotes]:
            self.opened += 1
            yield self

        return opened()

    def with_marker(self, offense_id: int, marker: str) -> list[str]:
        return [note for note in self.notes.get(offense_id, []) if note_run_marker(note) == marker]


@pytest.fixture
async def cases(sessions: Sessions) -> None:
    """The offense's case, and the case of the group the other offense was added to."""
    async with sessions.begin() as session:
        await create_case(
            session,
            case_id=CASE_ID,
            source=CaseSource.OFFENSE,
            offense_id=OFFENSE_ID,
            sla_due_at=T0,
            workflow_id=CASE_ID,
            run_id="run-1",
            evaluation_no=2,
        )
        await create_case(
            session,
            case_id=GROUP_CASE_ID,
            source=CaseSource.GROUP,
            group_id=GROUP_ID,
            sla_due_at=T0,
            workflow_id=GROUP_CASE_ID,
            run_id="run-2",
        )


@pytest.fixture
def qradar() -> FakeQRadar:
    return FakeQRadar()


@pytest.fixture
def writer(sessions: Sessions, cases: None) -> NoteWriter:
    return NoteWriter(sessions=sessions, clock=lambda: NOW)


async def switch(sessions: Sessions, enabled: bool, reason: str = "Canary başladı.") -> None:
    async with sessions.begin() as session:
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=enabled,
            reason=reason,
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )


async def write(writer: NoteWriter, qradar: FakeQRadar, request: NoteRequest) -> NoteOutcome:
    return await writer.write(request, qradar.open)


async def row(sessions: Sessions, offense_id: int, marker: str) -> NoteWrittenRow:
    async with sessions() as session:
        found = await get_note(session, offense_id=offense_id, run_marker=marker)
    assert found is not None
    return found


async def rows(sessions: Sessions) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(NoteWrittenRow)) or 0


async def note_audit(sessions: Sessions) -> list[AuditLogRow]:
    async with sessions() as session:
        return await list_audit(session, action="note.write")


# --- writing and recording -----------------------------------------------------------------


async def test_a_note_is_written_and_recorded(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    await switch(sessions, True)
    request = evaluation_note()

    outcome = await write(writer, qradar, request)

    assert outcome == NoteOutcome(
        result=NoteResult.WRITTEN, offense_id=OFFENSE_ID, run_marker=MARKER
    )
    assert qradar.reads == [(OFFENSE_ID, f"run:{MARKER}")]
    assert qradar.notes == {OFFENSE_ID: [render_note(request)]}
    recorded = await row(sessions, OFFENSE_ID, MARKER)
    assert (recorded.case_id, recorded.evaluation_no, recorded.status, recorded.error) == (
        CASE_ID,
        2,
        NoteStatus.WRITTEN,
        None,
    )
    assert recorded.written_at == NOW
    [entry] = await note_audit(sessions)
    assert (entry.actor_kind, entry.actor_id, entry.object_type, entry.object_id) == (
        ActorKind.SYSTEM,
        "action-executor",
        "offense",
        str(OFFENSE_ID),
    )
    assert entry.details == {
        "case_id": CASE_ID,
        "evaluation_no": 2,
        "run_marker": MARKER,
        "kind": "evaluation",
    }


@pytest.mark.parametrize(
    ("request_", "case_id", "kind"),
    [
        (group_note(), GROUP_CASE_ID, "group"),
        (no_decision_note(), CASE_ID, "no_ai_decision"),
    ],
)
async def test_every_kind_of_note_is_recorded_under_its_case(
    sessions: Sessions,
    writer: NoteWriter,
    qradar: FakeQRadar,
    request_: NoteRequest,
    case_id: str,
    kind: str,
) -> None:
    await switch(sessions, True)

    outcome = await write(writer, qradar, request_)

    assert outcome.result is NoteResult.WRITTEN
    assert qradar.notes == {request_.offense_id: [render_note(request_)]}
    recorded = await row(sessions, request_.offense_id, request_.run_marker)
    assert (recorded.case_id, recorded.status) == (case_id, NoteStatus.WRITTEN)
    [entry] = await note_audit(sessions)
    assert entry.details["kind"] == kind


# --- criterion 3: no note twice ------------------------------------------------------------


async def test_a_note_already_in_qradar_is_not_written_again(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    await switch(sessions, True)
    request = evaluation_note()
    qradar.notes[OFFENSE_ID] = ["Operatör notu: inceleniyor.", render_note(request)]

    outcome = await write(writer, qradar, request)

    assert outcome.result is NoteResult.SKIPPED_DUPLICATE
    assert qradar.adds == []
    assert len(qradar.with_marker(OFFENSE_ID, MARKER)) == 1
    assert (await row(sessions, OFFENSE_ID, MARKER)).status is NoteStatus.SKIPPED_DUPLICATE
    assert await note_audit(sessions) == []


async def test_a_retry_after_an_unacknowledged_write_leaves_one_note(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """The first attempt adds the note, but its answer is lost and the attempt fails before
    anything says the note was written. The retry finds the note and does not add it again."""
    await switch(sessions, True)
    request = evaluation_note()
    lost = OffenseNotesError(
        "add_offense_note: the gateway call failed (ReadTimeout)", retryable=True
    )
    qradar.add_failures.append((True, lost))

    with pytest.raises(OffenseNotesError) as raised:
        await write(writer, qradar, request)
    assert raised.value is lost
    first = await row(sessions, OFFENSE_ID, MARKER)
    assert first.status is NoteStatus.FAILED
    assert first.error == "add_offense_note: the gateway call failed (ReadTimeout)"

    outcome = await write(writer, qradar, request)

    assert outcome.result is NoteResult.SKIPPED_DUPLICATE
    assert len(qradar.adds) == 1
    assert len(qradar.with_marker(OFFENSE_ID, MARKER)) == 1
    retried = await row(sessions, OFFENSE_ID, MARKER)
    assert (retried.id, retried.status, retried.error) == (
        first.id,
        NoteStatus.SKIPPED_DUPLICATE,
        None,
    )
    assert await rows(sessions) == 1


async def test_a_note_recorded_as_written_is_not_looked_up_again(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """The activity recorded the note but its completion was lost: the retry ends at once."""
    await switch(sessions, True)
    request = evaluation_note()
    await write(writer, qradar, request)

    outcome = await write(writer, qradar, request)

    assert outcome.result is NoteResult.WRITTEN
    assert (qradar.opened, len(qradar.reads), len(qradar.adds)) == (1, 1, 1)
    assert len(await note_audit(sessions)) == 1


async def test_only_a_first_line_with_the_same_marker_counts(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """The lookup returns every note that holds `run:<marker>`; only the executor's own first
    line with exactly this marker makes a note a duplicate."""
    await switch(sessions, True)
    request = evaluation_note()
    header = render_note(request).split("\n")[0]
    qradar.notes[OFFENSE_ID] = [
        f"Operatör: run:{MARKER} notunu kontrol et.",
        f"Operatör notu\n{header}",
        header.replace(f"run:{MARKER}", f"run:{MARKER}d"),
        f"{header} ek",
    ]

    outcome = await write(writer, qradar, request)

    assert outcome.result is NoteResult.WRITTEN
    assert qradar.with_marker(OFFENSE_ID, MARKER) == [render_note(request)]


# --- criterion 4: the kill switch ----------------------------------------------------------


async def test_with_writes_never_switched_on_nothing_is_read_or_written(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """Shadow mode: the note is not written and QRadar is not even read. The record says
    `disabled`, not a failure, and holds no error text; the outcome says why."""
    outcome = await write(writer, qradar, evaluation_note())

    assert outcome.result is NoteResult.WRITES_DISABLED
    assert outcome.error is not None
    assert "never switched on (shadow mode)" in outcome.error
    assert qradar.opened == 0
    recorded = await row(sessions, OFFENSE_ID, MARKER)
    assert (recorded.status, recorded.error) == (NoteStatus.DISABLED, None)
    assert await note_audit(sessions) == []


async def test_a_switch_off_after_the_read_stops_the_write(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """Writes are on when the attempt starts; an admin switches them off while QRadar's notes
    are read. The switch is checked again right before the write, so nothing is written."""
    await switch(sessions, True)
    qradar.after_read = lambda: switch(sessions, False, "Notlar tekrar ediyor.\nİnceleniyor.")

    outcome = await write(writer, qradar, evaluation_note())

    assert outcome.result is NoteResult.WRITES_DISABLED
    assert outcome.error is not None
    assert "switched off by admin01 at " in outcome.error
    assert outcome.error.endswith(": Notlar tekrar ediyor. İnceleniyor.")
    assert len(qradar.reads) == 1
    assert qradar.adds == []
    assert qradar.notes == {}
    recorded = await row(sessions, OFFENSE_ID, MARKER)
    assert (recorded.status, recorded.error) == (NoteStatus.DISABLED, None)


async def test_a_disabled_note_is_written_once_writes_are_on(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """`disabled` does not end the work: the next attempt with writes on writes the note and
    updates the same record."""
    request = evaluation_note()
    assert (await write(writer, qradar, request)).result is NoteResult.WRITES_DISABLED
    disabled = await row(sessions, OFFENSE_ID, MARKER)
    assert disabled.status is NoteStatus.DISABLED

    await switch(sessions, True)
    outcome = await write(writer, qradar, request)

    assert outcome.result is NoteResult.WRITTEN
    assert qradar.with_marker(OFFENSE_ID, MARKER) == [render_note(request)]
    recorded = await row(sessions, OFFENSE_ID, MARKER)
    assert (recorded.id, recorded.status, recorded.error) == (
        disabled.id,
        NoteStatus.WRITTEN,
        None,
    )
    assert await rows(sessions) == 1
    assert len(await note_audit(sessions)) == 1


async def test_a_note_recorded_the_old_way_is_written_once_writes_are_on(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """A note T-019 recorded as `failed` with a `writes_disabled:` error before migration 0006
    moved it is tried again like any failure."""
    request = evaluation_note()
    async with sessions.begin() as session:
        await session.execute(
            insert(NoteWrittenRow).values(
                case_id=request.case_id,
                offense_id=OFFENSE_ID,
                evaluation_no=request.evaluation_no,
                run_marker=MARKER,
                status=NoteStatus.FAILED,
                error="writes_disabled: the kill switch is off",
                written_at=T0,
            )
        )
    await switch(sessions, True)

    outcome = await write(writer, qradar, request)

    assert outcome.result is NoteResult.WRITTEN
    recorded = await row(sessions, OFFENSE_ID, MARKER)
    assert (recorded.status, recorded.error) == (NoteStatus.WRITTEN, None)


# --- failures ------------------------------------------------------------------------------


async def test_a_refused_write_is_recorded_and_returned(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    """The gateway refused the call: a retry would be refused too, so the attempt does not
    fail; the outcome says why."""
    await switch(sessions, True)
    refused = OffenseNotesError(
        "add_offense_note: denied: invalid_text: note_text is longer than 2000 UTF-16 code units",
        retryable=False,
    )
    qradar.add_failures.append((False, refused))

    outcome = await write(writer, qradar, evaluation_note())

    assert outcome.result is NoteResult.FAILED
    assert outcome.error == str(refused)
    recorded = await row(sessions, OFFENSE_ID, MARKER)
    assert (recorded.status, recorded.error) == (NoteStatus.FAILED, str(refused))


async def test_a_failed_read_fails_the_attempt_and_the_retry_writes(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    await switch(sessions, True)
    qradar.read_failures.append(
        OffenseNotesError("get_offense_notes: error: upstream_unavailable\nretry", retryable=True)
    )

    with pytest.raises(OffenseNotesError):
        await write(writer, qradar, evaluation_note())
    assert qradar.adds == []
    failed = await row(sessions, OFFENSE_ID, MARKER)
    assert (failed.status, failed.error) == (
        NoteStatus.FAILED,
        "get_offense_notes: error: upstream_unavailable retry",
    )

    outcome = await write(writer, qradar, evaluation_note())

    assert outcome.result is NoteResult.WRITTEN
    assert (await row(sessions, OFFENSE_ID, MARKER)).status is NoteStatus.WRITTEN


async def test_an_invalid_request_touches_nothing(
    sessions: Sessions, writer: NoteWriter, qradar: FakeQRadar
) -> None:
    await switch(sessions, True)
    content = evaluation_note().content.model_copy(update={"case_url": "javascript:alert(1)"})
    request = evaluation_note().model_copy(update={"content": content})

    with pytest.raises(InvalidNote, match="case_url"):
        await write(writer, qradar, request)

    assert qradar.opened == 0
    assert await rows(sessions) == 0
