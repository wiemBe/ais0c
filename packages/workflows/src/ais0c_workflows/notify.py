"""What CaseWorkflow hands the Action Executor's activities (architecture §9; T-33, T-045).

This package may not import the executor (docs/impl/repo-structure.md), so the request bodies of
the executor's two activities are this package's own models. They mirror
`ais0c_executor.note.request` and `ais0c_executor.email.request` field for field, kind markers
included: the payload converter validates the JSON against the executor's types when the
activity decodes it, and the executor's `validated()` check is the authority. A worker test
keeps the two sides together (services/worker/tests/test_executor_requests.py).

Also here, all deterministic (criteria 3, 4 and 8):

- the run marker of a note: the first 12 hex characters of the hash of the case, the evaluation
  and the note's kind (T-33 (3)); the same note gets the same marker on every attempt, so a late
  decision writes its own note beside the no-decision one (D-30);
- the `NoteContent` of a decision, built from the Reporting agent's report; without a report the
  note carries the fixed Turkish sentence and no urgent events;
- the levels that get an alert e-mail, and the retry policy and timeouts of the executor's
  activities.
"""

import hashlib
from datetime import timedelta
from typing import Annotated, Final, Literal

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field

    from ais0c_contracts import (
        CaseReport,
        CaseVerdict,
        Confidence,
        Level,
        NoteContent,
        UtcDatetime,
    )

# The note's kind, the marker's third part (T-33 (3)): the executor's `NoteKind` values. The
# first two are also the request's discriminator; a group note is an `EvaluationNoteRequest` with
# `content.group_id` set, and T-027 writes it.
EVALUATION_NOTE_KIND: Final = "evaluation"
NO_AI_DECISION_NOTE_KIND: Final = "no_ai_decision"
GROUP_NOTE_KIND: Final = "group"
type NoteKind = Literal["evaluation", "no_ai_decision", "group"]
CASE_ALERT_KIND: Final = "case_alert"

# The levels whose evaluation is e-mailed (architecture §9, D-22). Whether a re-evaluation is
# e-mailed again is the executor's rule (D-42): only above the levels already sent.
ALERT_LEVELS: Final = frozenset({Level.HIGH, Level.CRITICAL})

# How many urgent events the note carries: the first by rank (architecture §9).
NOTE_URGENT_EVENTS: Final = 5
# `NoteContent.summary_tr` is shorter than the report's `summary_tr`; the note takes the start.
SUMMARY_MAX: Final = 400
# The summary of a decision Reporting gave no report for (criterion 4).
NO_REPORT_SUMMARY_TR: Final = "AI özeti üretilemedi; karar ve kanıt platformda."

# The executor's activities raise only what a retry may get past (gateway, QRadar or relay
# unreachable, a 4xx reply); they return a refusal a retry would get again as a `failed`
# outcome (T-019, T-020). So everything they raise is retried, with backoff, except a note or
# e-mail that cannot be built (`InvalidNote`, `InvalidEmail`), until EXECUTOR_TOTAL_TIMEOUT.
EXECUTOR_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    non_retryable_error_types=["InvalidNote", "InvalidEmail"],
)
# One attempt: the gateway calls of a note may wait for their quota pool (up to 60 seconds
# each) and the relay answers slowly.
EXECUTOR_ATTEMPT_TIMEOUT: Final = timedelta(minutes=5)
# The whole call, from the moment it is scheduled: the retries and a stopped executor worker
# included. A note or e-mail still not done then is given up (criterion 8); the case does not
# wait for it longer than that when it closes or continues as new.
EXECUTOR_TOTAL_TIMEOUT: Final = timedelta(hours=1)


class EvaluationNoteRequest(BaseModel):
    """The body of the executor's `EvaluationNote`: a decision's note."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["evaluation"] = EVALUATION_NOTE_KIND
    case_id: str
    evaluated_at: UtcDatetime
    """When the decision was recorded; the time on the note's first line."""
    content: NoteContent


class NoDecisionNoteRequest(BaseModel):
    """The body of the executor's `NoDecisionNote`: "AI değerlendirmesi yapılamadı"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["no_ai_decision"] = NO_AI_DECISION_NOTE_KIND
    case_id: str
    offense_id: int
    evaluation_no: int
    run_marker: str
    evaluated_at: UtcDatetime
    """When the evaluation ended without a decision; the time on the note's first line."""
    case_url: str


type NoteRequest = Annotated[
    EvaluationNoteRequest | NoDecisionNoteRequest, Field(discriminator="kind")
]


class CaseAlertRequest(BaseModel):
    """The body of the executor's `CaseAlert`: the e-mail of a high or critical evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["case_alert"] = CASE_ALERT_KIND
    case_id: str
    offense_name: str
    """The offense's description as QRadar holds it; the executor cleans and cuts it."""
    evaluated_at: UtcDatetime
    content: NoteContent
    """The fields of the evaluation's QRadar note."""


def run_marker(case_id: str, evaluation_no: int, kind: NoteKind) -> str:
    """The note's run marker: 12 hex characters of `sha256("<case_id>:<evaluation_no>:<kind>")`
    (T-33 (3)).

    The same note gets the same marker on every attempt, and another evaluation or kind gets
    another one, so the executor can tell the notes of one offense apart.
    """
    key = f"{case_id}:{evaluation_no}:{kind}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def note_content(
    *,
    case_id: str,
    offense_id: int,
    evaluation_no: int,
    case_url: str,
    verdict: CaseVerdict,
    confidence: Confidence,
    notify_level: Level,
    report: CaseReport | None,
) -> NoteContent:
    """The note's content of a decision, built from the report deterministically (criterion 4).

    With a report: its summary (cut to the note's shorter limit, ending in "…"), its first
    urgent events by rank, its recommendations' action types (each once, in order) and its data
    gaps. Without one (Reporting gave no result): the fixed Turkish sentence, no urgent events,
    no actions and no gaps; the decision itself is still on the note's second line.
    """
    if report is None:
        summary_tr, urgent_events, actions, data_gaps = NO_REPORT_SUMMARY_TR, [], [], []
    else:
        summary_tr = _cut(report.summary_tr, SUMMARY_MAX)
        urgent_events = sorted(report.urgent_events, key=lambda event: event.rank)
        actions = list(dict.fromkeys(item.action_type for item in report.recommendations))
        data_gaps = list(report.data_gaps)
    return NoteContent(
        offense_id=offense_id,
        evaluation_no=evaluation_no,
        run_marker=run_marker(case_id, evaluation_no, EVALUATION_NOTE_KIND),
        verdict=verdict,
        confidence=confidence,
        notify_level=notify_level,
        summary_tr=summary_tr,
        urgent_events=urgent_events[:NOTE_URGENT_EVENTS],
        recommended_actions=actions,
        data_gaps=data_gaps,
        case_url=case_url,
    )


def evaluation_note(
    *, case_id: str, evaluated_at: UtcDatetime, content: NoteContent
) -> EvaluationNoteRequest:
    """The decision note of an evaluation, from its recorded content."""
    return EvaluationNoteRequest(case_id=case_id, evaluated_at=evaluated_at, content=content)


def no_decision_note(
    *,
    case_id: str,
    offense_id: int,
    evaluation_no: int,
    case_url: str,
    evaluated_at: UtcDatetime,
) -> NoDecisionNoteRequest:
    """The note of an evaluation that ended without an AI decision."""
    return NoDecisionNoteRequest(
        case_id=case_id,
        offense_id=offense_id,
        evaluation_no=evaluation_no,
        run_marker=run_marker(case_id, evaluation_no, NO_AI_DECISION_NOTE_KIND),
        evaluated_at=evaluated_at,
        case_url=case_url,
    )


def case_alert(
    *, case_id: str, offense_name: str, evaluated_at: UtcDatetime, content: NoteContent
) -> CaseAlertRequest:
    """The alert e-mail of an evaluation whose level is high or critical."""
    return CaseAlertRequest(
        case_id=case_id,
        offense_name=offense_name,
        evaluated_at=evaluated_at,
        content=content,
    )


def _cut(text: str, limit: int) -> str:
    """`text`, or its start ending in "…" when it is longer than `limit`."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
