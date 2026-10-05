"""What a note is written from, and what writing it came to (architecture §9).

Two requests make the three kinds of note:

- `EvaluationNote`: an evaluation's decision, from the Reporting agent's structured output
  (`NoteContent`). With `content.group_id` set, it is the short note of an offense added to a
  group, which carries the group's decision.
- `NoDecisionNote`: "AI değerlendirmesi yapılamadı", for an evaluation that ended without an AI
  decision (architecture §9, "Ajan SLA'sı"). `NoteContent` cannot carry it, because its verdict
  and confidence are required.

Both carry the note's identity: the offense, the evaluation number, the run marker and the case
the note is recorded under (`notes_written.case_id`; for a group note, the group's case). The
run marker must be the same on every attempt to write one note and differ between the notes
of an offense; the writer finds a note it already wrote by it. Identifiers and the case link
are platform values, checked here so they cannot change a note's layout.
"""

import re
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

import pydantic_core
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ais0c_contracts import NoteContent, UtcDatetime
from ais0c_executor.note.errors import InvalidNote

# The marker ends the note's first line: `run:<marker>`.
RUN_MARKER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}")
# Workflow-derived IDs: `case-12345`, `group-<id>` (the policy package's context ID form).
CASE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
# `G-<key>-<time>` (ais0c_activities.grouping).
GROUP_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
# The platform's case page: http(s), a host name or address, an optional port and a path.
CASE_URL: Final = re.compile(
    r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/?#=&+-]*)?"
)
MAX_CASE_URL_LENGTH: Final = 200
MAX_EVALUATION_NO: Final = 99_999
MAX_OFFENSE_ID: Final = 2**63 - 1


class NoteKind(StrEnum):
    EVALUATION = "evaluation"
    GROUP = "group"
    NO_AI_DECISION = "no_ai_decision"


class EvaluationNote(BaseModel):
    """The note of an evaluation's decision; the short group note when `content.group_id` is
    set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["evaluation"] = "evaluation"
    case_id: str
    evaluated_at: UtcDatetime
    """When the decision was made; the time on the note's first line."""
    content: NoteContent

    @model_validator(mode="after")
    def _check(self) -> Self:
        content = self.content
        check_identity(
            case_id=self.case_id,
            offense_id=content.offense_id,
            evaluation_no=content.evaluation_no,
            run_marker=content.run_marker,
            case_url=content.case_url,
        )
        if content.group_id is not None and not GROUP_ID.fullmatch(content.group_id):
            raise ValueError("group_id must be 1-64 letters, digits, '.', '_', ':' and '-'")
        return self

    @property
    def note_kind(self) -> NoteKind:
        return NoteKind.EVALUATION if self.content.group_id is None else NoteKind.GROUP

    @property
    def offense_id(self) -> int:
        return self.content.offense_id

    @property
    def evaluation_no(self) -> int:
        return self.content.evaluation_no

    @property
    def run_marker(self) -> str:
        return self.content.run_marker


class NoDecisionNote(BaseModel):
    """The "AI değerlendirmesi yapılamadı" note: the AI did not evaluate the offense, so the
    operator does not take it for one the AI has looked at."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["no_ai_decision"] = "no_ai_decision"
    case_id: str
    offense_id: int
    evaluation_no: int
    run_marker: str
    evaluated_at: UtcDatetime
    """When the evaluation ended without a decision; the time on the note's first line."""
    case_url: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_identity(
            case_id=self.case_id,
            offense_id=self.offense_id,
            evaluation_no=self.evaluation_no,
            run_marker=self.run_marker,
            case_url=self.case_url,
        )
        return self

    @property
    def note_kind(self) -> NoteKind:
        return NoteKind.NO_AI_DECISION


type NoteRequest = Annotated[EvaluationNote | NoDecisionNote, Field(discriminator="kind")]

_REQUEST: Final[TypeAdapter[NoteRequest]] = TypeAdapter(NoteRequest)


def validated(request: NoteRequest) -> NoteRequest:
    """`request` validated again from its JSON form, so one built with `model_construct()` is
    checked too. Raises `InvalidNote`, naming the fields but not their values."""
    try:
        return _REQUEST.validate_json(pydantic_core.to_json(request))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'request'}: {item['msg']}"
            for item in error.errors(include_url=False, include_input=False, include_context=False)
        )
        raise InvalidNote(f"invalid note request: {problems}") from None


class NoteResult(StrEnum):
    """What a write came to.

    `writes_disabled`: the kill switch was off (T-23), so nothing was written. `notes_written`
    records it as `disabled`, without an error text; it is not a failure (T-37), and a later
    attempt with writes on writes the note.
    """

    WRITTEN = "written"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    WRITES_DISABLED = "writes_disabled"
    FAILED = "failed"


class NoteOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: NoteResult
    offense_id: int
    run_marker: str
    error: str | None = None
    """Why the note was not written, for `writes_disabled` and `failed`."""


def check_identity(
    *, case_id: str, offense_id: int, evaluation_no: int, run_marker: str, case_url: str
) -> None:
    """Raise ValueError unless the note's identifiers and case link are usable."""
    if not CASE_ID.fullmatch(case_id):
        raise ValueError("case_id must be a workflow-derived ID such as case-12345")
    if not 0 <= offense_id <= MAX_OFFENSE_ID:
        raise ValueError("offense_id must be a QRadar offense ID")
    if not 1 <= evaluation_no <= MAX_EVALUATION_NO:
        raise ValueError(f"evaluation_no must be between 1 and {MAX_EVALUATION_NO}")
    if not RUN_MARKER.fullmatch(run_marker):
        raise ValueError("run_marker must be 1-64 letters, digits and hyphens")
    if len(case_url) > MAX_CASE_URL_LENGTH or not CASE_URL.fullmatch(case_url):
        raise ValueError(
            f"case_url must be an http(s) link of at most {MAX_CASE_URL_LENGTH} characters"
        )
