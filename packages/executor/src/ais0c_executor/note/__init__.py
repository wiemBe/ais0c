"""QRadar offense notes (architecture §9, "QRadar offense notu"; D-18).

Every analyzed offense gets a note, written by the executor from a fixed Turkish template, never
by a model:

- `render_note`: the note's text from an `EvaluationNote` (an evaluation's decision, or the
  short note of an offense added to a group) or a `NoDecisionNote` ("AI değerlendirmesi
  yapılamadı").
- `NoteWriter`: writes a note once. It skips a note QRadar already has, checks the kill switch
  right before the write and records the outcome in `notes_written`.

QRadar is reached through an `OffenseNotes`; the activity `write_offense_note`
(`ais0c_activities.note`) gives the writer one that calls the MCP Policy Gateway with the
`qradar-note-write` profile.
"""

from ais0c_executor.note.errors import InvalidNote, OffenseNotesError
from ais0c_executor.note.render import (
    MAX_NOTE_LENGTH,
    NOTE_TEMPLATES,
    NOTE_TIME_ZONE,
    note_run_marker,
    render_note,
)
from ais0c_executor.note.request import (
    EvaluationNote,
    NoDecisionNote,
    NoteKind,
    NoteOutcome,
    NoteRequest,
    NoteResult,
)
from ais0c_executor.note.writer import (
    EXECUTOR_ID,
    NOTE_AUDIT_ACTION,
    NOTE_OBJECT_TYPE,
    WRITES_DISABLED_PREFIX,
    NoteWriter,
    OffenseNotes,
    OpenNotes,
)

__all__ = [
    "EXECUTOR_ID",
    "MAX_NOTE_LENGTH",
    "NOTE_AUDIT_ACTION",
    "NOTE_OBJECT_TYPE",
    "NOTE_TEMPLATES",
    "NOTE_TIME_ZONE",
    "WRITES_DISABLED_PREFIX",
    "EvaluationNote",
    "InvalidNote",
    "NoDecisionNote",
    "NoteKind",
    "NoteOutcome",
    "NoteRequest",
    "NoteResult",
    "NoteWriter",
    "OffenseNotes",
    "OffenseNotesError",
    "OpenNotes",
    "note_run_marker",
    "render_note",
]
