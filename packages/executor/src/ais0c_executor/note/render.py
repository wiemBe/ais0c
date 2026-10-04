"""The text of a QRadar offense note (architecture §9, "QRadar offense notu").

The executor writes the note, not a model: it fills the fixed Turkish templates in `templates/`
with structured fields. Raw log text never goes in. Every field is one cleaned line
(`clean_text`) cut to a length, so text from a log cannot add a line of its own, fake a note
header or hide characters; the template alone decides the layout.

Every note starts with the same line, times in Europe/Istanbul time:

    [AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c

`note_run_marker` reads the marker back from a note's first line; the writer uses it to find a
note it already wrote.

QRadar takes a note only within two limits, both measured in the lab (QRadar 7.6.0):

- MAX_NOTE_LENGTH: 2000 characters, counted in UTF-16 code units as Java counts them, so a
  character outside the Basic Multilingual Plane, such as an emoji, counts twice. A longer note
  is refused: "note_text must be between 1 and 2000 characters".
- MAX_NOTE_URL_LENGTH: QRadar's API takes the note in the URL
  (`POST /siem/offenses/{id}/notes?note_text=...`, as the qradar-mcp fork sends it), and its web
  server refuses a request line longer than 8190 bytes. A 2000-character note of Turkish
  letters, 11 774 characters once percent-encoded, was refused with 414; 8099 characters passed
  and 8159 did not. The encoded note stays well below that.

An evaluation note that would not fit gets shorter urgent event lines first, then leaves out the
lowest-ranked events (a line says how many more are in the detailed report), and last cuts the
summary and the data gaps.
"""

import re
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from ais0c_contracts import ActionType, DataGap, NoteContent, UrgentEvent
from ais0c_executor.common import FieldValue, Templates, clean_text
from ais0c_executor.note.errors import InvalidNote
from ais0c_executor.note.request import (
    RUN_MARKER,
    EvaluationNote,
    NoDecisionNote,
    NoteRequest,
    validated,
)

NOTE_TEMPLATES: Final = Templates(Path(__file__).parent / "templates")
MAX_NOTE_LENGTH: Final = 2000
MAX_NOTE_URL_LENGTH: Final = 7000
NOTE_TIME_ZONE: Final = ZoneInfo("Europe/Istanbul")
QID_LENGTH: Final = 12

_HEADER: Final = re.compile(rf"\[AI-SOC\] .* · run:(?P<marker>{RUN_MARKER.pattern})")


@dataclass(frozen=True)
class _Shape:
    """How long the parts of an evaluation note may be."""

    log_source: int
    event_name: int
    address: int
    username: int
    reason: int
    summary: int
    gaps: int
    gap_source: int


# From the fullest to the shortest. The event's identifiers find it in QRadar, so its reason is
# cut before them. The summary's own limit in NoteContent is 400.
_FULL: Final = _Shape(
    log_source=60,
    event_name=80,
    address=64,
    username=48,
    reason=200,
    summary=400,
    gaps=3,
    gap_source=40,
)
_MEDIUM: Final = _Shape(
    log_source=60,
    event_name=80,
    address=64,
    username=48,
    reason=100,
    summary=400,
    gaps=3,
    gap_source=40,
)
_COMPACT: Final = _Shape(
    log_source=40,
    event_name=48,
    address=40,
    username=32,
    reason=80,
    summary=400,
    gaps=3,
    gap_source=40,
)
_MINIMAL: Final = _Shape(
    log_source=24,
    event_name=32,
    address=40,
    username=24,
    reason=60,
    summary=160,
    gaps=1,
    gap_source=20,
)


def render_note(request: NoteRequest) -> str:
    """The note's text. Raises `InvalidNote` if the request is invalid or the note cannot fit
    QRadar's limits."""
    request = validated(request)
    if isinstance(request, NoDecisionNote):
        text = NOTE_TEMPLATES.render(
            "no_decision_note.txt",
            **_header(request.evaluation_no, request.evaluated_at, request.run_marker),
            case_url=request.case_url,
        )
    elif request.content.group_id is not None:
        content = request.content
        text = NOTE_TEMPLATES.render(
            "group_note.txt",
            **_header(content.evaluation_no, request.evaluated_at, content.run_marker),
            group_id=content.group_id,
            verdict=content.verdict.value,
            case_url=content.case_url,
        )
    else:
        text = _evaluation_note(request)
    if not fits(text):
        raise InvalidNote("the note does not fit QRadar's limits")
    return text


def note_length(text: str) -> int:
    """The note's length as QRadar counts it: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def note_url_length(text: str) -> int:
    """The note's length percent-encoded in a URL query, as the fork's HTTP client sends it."""
    return len(urllib.parse.quote_plus(text))


def fits(text: str) -> bool:
    """Whether QRadar takes `text` as a note: not empty, and within both limits."""
    return 0 < note_length(text) <= MAX_NOTE_LENGTH and note_url_length(text) <= MAX_NOTE_URL_LENGTH


def note_run_marker(text: str) -> str | None:
    """The run marker on the first line of a note the executor wrote; None for any other note."""
    lines = text.splitlines()
    match = _HEADER.fullmatch(lines[0].strip()) if lines else None
    return None if match is None else match.group("marker")


def _evaluation_note(request: EvaluationNote) -> str:
    """The first of these that fits: every event in a shorter and shorter shape, then fewer
    events, then the minimal shape with fewer and fewer events."""
    events = sorted(request.content.urgent_events, key=lambda event: event.rank)
    attempts = [
        (_FULL, len(events)),
        (_MEDIUM, len(events)),
        *((_COMPACT, shown) for shown in range(len(events), -1, -1)),
        *((_MINIMAL, shown) for shown in range(len(events), -1, -1)),
    ]
    for shape, shown in attempts:
        text = _render_evaluation(request, events, shape, shown)
        if fits(text):
            return text
    raise InvalidNote("the note does not fit QRadar's limits")


def _render_evaluation(
    request: EvaluationNote, events: Sequence[UrgentEvent], shape: _Shape, shown: int
) -> str:
    content: NoteContent = request.content
    day = _local(request.evaluated_at).date()
    gaps = content.data_gaps[: shape.gaps]
    return NOTE_TEMPLATES.render(
        "offense_note.txt",
        **_header(content.evaluation_no, request.evaluated_at, content.run_marker),
        verdict=content.verdict.value,
        confidence=content.confidence.value,
        notify_level=content.notify_level.value,
        summary=clean_text(content.summary_tr, shape.summary) or "-",
        events=[_event(event, shape, day) for event in events[:shown]],
        omitted=len(events) - shown,
        actions=_actions(content.recommended_actions),
        data_gaps=[_gap(gap, shape, day) for gap in gaps],
        more_gaps=len(content.data_gaps) - len(gaps),
        case_url=content.case_url,
    )


def _header(evaluation_no: int, evaluated_at: datetime, run_marker: str) -> dict[str, FieldValue]:
    return {
        "evaluation_no": evaluation_no,
        "evaluated_at": f"{_local(evaluated_at):%Y-%m-%d %H:%M}",
        "run_marker": run_marker,
    }


def _event(event: UrgentEvent, shape: _Shape, day: date) -> dict[str, FieldValue]:
    """The identifiers that find the event in QRadar and why it matters; nothing else of it
    (not its checklist, AQL or evidence ID) goes into the note."""
    return {
        "rank": event.rank,
        "time": _event_time(event.time, day),
        "log_source": clean_text(event.log_source, shape.log_source) or "-",
        "event_name": clean_text(event.event_name, shape.event_name) or "-",
        "qid": None if event.qid is None else clean_text(str(event.qid), QID_LENGTH),
        "source": _optional(event.source, shape.address),
        "destination": _optional(event.destination, shape.address),
        "username": _optional(event.username, shape.username),
        "reason": clean_text(event.reason, shape.reason) or "-",
    }


def _gap(gap: DataGap, shape: _Shape, day: date) -> dict[str, FieldValue]:
    return {
        "source": clean_text(gap.source, shape.gap_source) or "-",
        "reason": gap.reason.value,
        "start": _gap_time(gap.period_start, day),
        "end": _gap_time(gap.period_end, day),
    }


def _actions(actions: Sequence[ActionType]) -> list[FieldValue]:
    """The recommended action types, each once, in the order given."""
    return list(dict.fromkeys(action.value for action in actions))


def _optional(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return clean_text(value, max_length) or None


def _local(value: datetime) -> datetime:
    return value.astimezone(NOTE_TIME_ZONE)


def _event_time(value: datetime, day: date) -> str:
    """The time alone on the note's day, the date and time on another day."""
    local = _local(value)
    return f"{local:%H:%M:%S}" if local.date() == day else f"{local:%Y-%m-%d %H:%M:%S}"


def _gap_time(value: datetime, day: date) -> str:
    local = _local(value)
    return f"{local:%H:%M}" if local.date() == day else f"{local:%Y-%m-%d %H:%M}"
