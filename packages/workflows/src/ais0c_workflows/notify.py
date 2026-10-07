"""What CaseWorkflow and GroupCaseWorkflow hand the Action Executor's activities (architecture
§9; T-33, T-045, T-027).

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
- the group note of an offense a group took (T-027): the group's decision with `group_id` set,
  which the executor writes in the short group format; its marker is the group case's, the same
  on every offense of the group, so each offense gets one note per group decision (the executor
  keeps `notes_written` unique per offense and marker);
- the group's alert e-mail, built from the group's decision as a case alert is from a case's;
- the levels that get an alert e-mail, and the retry policy and timeouts of the executor's
  activities;
- the notification of a health alarm (`HealthAlarmNotice`, T-032), which HealthCheck hands the
  executor's e-mail and the syslog channel, and `AbandonedCall`, what a case tells the case
  queue about a note or e-mail it gave up on (`abandoned_call`).
"""

import hashlib
from datetime import timedelta
from typing import Annotated, Final, Literal

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field

    from ais0c_contracts import (
        ActionType,
        CaseReport,
        CaseVerdict,
        Confidence,
        Level,
        NoteContent,
        UrgentEvent,
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
GROUP_ALERT_KIND: Final = "group_alert"
HEALTH_ALARM_KIND: Final = "health_alarm"

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


class GroupAlertRequest(BaseModel):
    """The body of the executor's `GroupAlert`: the e-mail of a group whose level is high or
    critical (D-22, D-42)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["group_alert"] = GROUP_ALERT_KIND
    case_id: str
    """The group's case, `group-<group_id>`."""
    group_id: str
    title: str
    """What the group's offenses share: the description QRadar gives the offense the summary
    shows; the executor cleans and cuts it."""
    offense_count: int
    evaluation_no: int
    evaluated_at: UtcDatetime
    """When the group's decision was recorded; the time the e-mail shows."""
    verdict: CaseVerdict
    confidence: Confidence
    notify_level: Level
    summary_tr: str
    urgent_events: list[UrgentEvent]
    recommended_actions: list[ActionType]
    case_url: str


type EmailRequest = Annotated[CaseAlertRequest | GroupAlertRequest, Field(discriminator="kind")]


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


def group_note(
    *,
    case_id: str,
    group_id: str,
    offense_id: int,
    evaluated_at: UtcDatetime,
    decision: NoteContent,
) -> EvaluationNoteRequest:
    """The group note of an offense the group took: the group's `decision` (its evaluation's
    note content) with the offense's ID, the group's ID and the group marker (T-027 criterion
    2)."""
    content = decision.model_copy(
        update={
            "offense_id": offense_id,
            "run_marker": run_marker(case_id, decision.evaluation_no, GROUP_NOTE_KIND),
            "group_id": group_id,
        }
    )
    return EvaluationNoteRequest(
        case_id=case_id,
        evaluated_at=evaluated_at,
        content=NoteContent.model_validate(content.model_dump()),
    )


def group_alert(
    *,
    case_id: str,
    group_id: str,
    title: str,
    offense_count: int,
    evaluated_at: UtcDatetime,
    decision: NoteContent,
) -> GroupAlertRequest:
    """The alert e-mail of a group decision whose level is high or critical, with the fields
    of its note."""
    return GroupAlertRequest(
        case_id=case_id,
        group_id=group_id,
        title=title,
        offense_count=offense_count,
        evaluation_no=decision.evaluation_no,
        evaluated_at=evaluated_at,
        verdict=decision.verdict,
        confidence=decision.confidence,
        notify_level=decision.notify_level,
        summary_tr=decision.summary_tr,
        urgent_events=list(decision.urgent_events),
        recommended_actions=list(decision.recommended_actions),
        case_url=decision.case_url,
    )


type HealthAlarmKindName = Literal[
    "intake_stopped", "log_source_silent", "write_failures", "executor_absent"
]
type HealthAlarmState = Literal["open", "reminder", "resolved"]


class HealthAlarmNotice(BaseModel):
    """One notification of a health alarm, as the checks return it and the executor's
    `HealthAlarm` takes it (the e-mail) and the syslog activity (T-032)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["health_alarm"] = HEALTH_ALARM_KIND
    alarm_id: str
    alarm_kind: HealthAlarmKindName
    subject: str
    subject_name: str | None = None
    status: HealthAlarmState
    notification_no: int
    opened_at: UtcDatetime
    counts: dict[str, int] = Field(default_factory=dict[str, int])


class AbandonedCall(BaseModel):
    """A note or e-mail the case gave up on, as `record_executor_failure` takes it; it mirrors
    `ais0c_activities.executor_failure.AbandonedCall` (T-032, T-59 (7))."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["note", "email"]
    case_id: str
    evaluation_no: int
    offense_id: int | None = None
    run_marker: str | None = None
    email_kind: Literal["case_alert", "group_alert"] | None = None
    level: Level | None = None
    group_id: str | None = None
    idempotency_key: str | None = None


def abandoned_call(
    request: "EvaluationNoteRequest | NoDecisionNoteRequest | CaseAlertRequest | GroupAlertRequest",
) -> AbandonedCall:
    """What the executor's record of the call needs, from the request the case gave up on.

    The e-mail key is the executor's own: `case_alert:<case_id>:<evaluation_no>` and
    `group_alert:<group_id>:<evaluation_no>`, so a row the executor wrote itself is the same row.
    """
    if isinstance(request, EvaluationNoteRequest):
        content = request.content
        return AbandonedCall(
            kind="note",
            case_id=request.case_id,
            evaluation_no=content.evaluation_no,
            offense_id=content.offense_id,
            run_marker=content.run_marker,
        )
    if isinstance(request, NoDecisionNoteRequest):
        return AbandonedCall(
            kind="note",
            case_id=request.case_id,
            evaluation_no=request.evaluation_no,
            offense_id=request.offense_id,
            run_marker=request.run_marker,
        )
    if isinstance(request, CaseAlertRequest):
        content = request.content
        return AbandonedCall(
            kind="email",
            case_id=request.case_id,
            evaluation_no=content.evaluation_no,
            email_kind=CASE_ALERT_KIND,
            level=content.notify_level,
            idempotency_key=f"case_alert:{request.case_id}:{content.evaluation_no}",
        )
    return AbandonedCall(
        kind="email",
        case_id=request.case_id,
        evaluation_no=request.evaluation_no,
        email_kind=GROUP_ALERT_KIND,
        level=request.notify_level,
        group_id=request.group_id,
        idempotency_key=f"group_alert:{request.group_id}:{request.evaluation_no}",
    )
