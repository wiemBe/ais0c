"""The subject and body of an alert e-mail (architecture §9, "E-posta bildirimi").

The executor writes the e-mail, not a model. `alert_message` turns a request into an
`EmailMessage` (the contract) and `render_body` fills the message's Turkish template with its
fields. Raw log text never goes in. Every field is one cleaned line (`clean_text`) cut to a
length, so text from a log cannot add a line of its own, fake a header or hide characters; the
templates in `templates/` alone decide the layout.

- A health alarm (T-032) has a subject and a body of its own: the alarm's kind, state and
  subject, the numbers it was raised with and when it opened; nothing of a model's.
- Subject: the level, the AI's verdict and the offense's name (a group's title), at most 150
  characters. The name is cleaned and cut until the subject fits.
- Body: the fields of the case's QRadar note (summary, urgent events, recommended actions, data
  gaps) and the link to the platform.

The contract's `fields` are plain strings, so lists are spread over fields. `event_1` to
`event_5` hold the urgent events, one line each, rendered by `templates/parts/event.txt`; the
recommended actions and the data gaps are one line each. The contract values the e-mail shows
(`level`, `verdict`, `confidence`, an action type, a data gap reason) go in with their Turkish
labels from `ais0c_executor.common.label`, the same table the QRadar note uses; `verdict_is_fp`
tells the template whether to add the false-positive line. A value without a label is an error.

Times are shown in Europe/Istanbul time.
"""

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ais0c_contracts import ActionType, CaseVerdict, DataGap, EmailKind, EmailMessage, UrgentEvent
from ais0c_executor.common import FieldValue, TemplateError, Templates, clean_text, is_clean, label
from ais0c_executor.email.errors import InvalidEmail
from ais0c_executor.email.request import (
    CaseAlert,
    EmailRequest,
    GroupAlert,
    HealthAlarm,
    validated,
)

EMAIL_TEMPLATES: Final = Templates(Path(__file__).parent / "templates")
EMAIL_TIME_ZONE: Final = ZoneInfo("Europe/Istanbul")
# `EmailMessage.subject`.
MAX_SUBJECT_LENGTH: Final = 150
# A body this long means something upstream went wrong; no alert comes near it.
MAX_BODY_LENGTH: Final = 20_000
# The template of each kind of e-mail; a hunt report (phase 3) has none yet.
TEMPLATE_IDS: Final = {
    EmailKind.CASE_ALERT: "case_alert",
    EmailKind.GROUP_ALERT: "group_alert",
    EmailKind.HEALTH_ALARM: "health_alarm",
}
MAX_EVENTS: Final = 5
MAX_GAPS: Final = 5
QID_LENGTH: Final = 12

# Lengths of the parts of the body. The summary and an event's fields are within their contract
# limits already; the cut matters for a model's output built with `model_construct()`.
_NAME: Final = 300
_SUMMARY: Final = 400
_LOG_SOURCE: Final = 120
_EVENT_NAME: Final = 200
_ADDRESS: Final = 100
_USERNAME: Final = 100
_REASON: Final = 300
_GAP_SOURCE: Final = 80
_SUBJECT_NAME: Final = 120

# The Turkish words of a health alarm: what it is about, its state and its numbers.
_ALARM_KINDS: Final = {
    "intake_stopped": "Offense akışı durdu",
    "log_source_silent": "Log source sustu",
    "write_failures": "Not/e-posta hataları arttı",
    "executor_absent": "Executor worker'ı yok",
}
_ALARM_STATES: Final = {"open": "AÇILDI", "reminder": "HATIRLATMA", "resolved": "DÜZELDİ"}
_COUNT_LABELS: Final = {
    "lag_minutes": "Gecikme (dk)",
    "silent_minutes": "Sessizlik (dk)",
    "absent_minutes": "Yokluk (dk)",
    "failures": "Hata sayısı",
    "threshold": "Eşik",
    "window_minutes": "Pencere (dk)",
    "pollers": "Worker sayısı",
}


def alert_message(request: EmailRequest, recipients: Sequence[str]) -> EmailMessage:
    """The e-mail of `request` to `recipients`, with its subject and its template's fields.

    The recipients are not checked here; the sender checks them against the allowed domains.
    Raises `InvalidEmail` if the request is invalid or its e-mail cannot be built.
    """
    request = validated(request)
    if isinstance(request, HealthAlarm):
        fields = _health_fields(request)
        subject = _subject(
            "subject/health_alarm.txt",
            name_field="subject",
            name=request.subject,
            state=_ALARM_STATES[request.status],
            kind=_ALARM_KINDS[request.alarm_kind],
        )
    elif isinstance(request, CaseAlert):
        content = request.content
        fields = _case_fields(request)
        subject = _subject(
            "subject/case_alert.txt",
            name_field="offense_name",
            name=request.offense_name,
            level=label("level_tag", content.notify_level),
            verdict=label("verdict_short", content.verdict),
            offense_id=content.offense_id,
        )
    else:
        fields = _group_fields(request)
        subject = _subject(
            "subject/group_alert.txt",
            name_field="title",
            name=request.title,
            level=label("level_tag", request.notify_level),
            verdict=label("verdict_short", request.verdict),
            offense_count=request.offense_count,
        )
    try:
        message = EmailMessage(
            kind=request.email_kind,
            recipients=list(recipients),
            subject=subject,
            template_id=TEMPLATE_IDS[request.email_kind],
            fields=fields,
            attachments=[],
            idempotency_key=request.idempotency_key,
        )
    except ValidationError as error:
        raise InvalidEmail(f"the e-mail cannot be built: {error.error_count()} problems") from None
    # Fails here rather than at the relay if the fields do not fill the template.
    render_body(message)
    return message


def render_body(message: EmailMessage) -> str:
    """The plain-text body of `message`, ending in a line break.

    Raises `InvalidEmail` if the message cannot be sent as it is: its template is not the one of
    its kind, its fields do not fill the template, its subject is empty, longer than 150
    characters or more than one clean line, or it has attachments (hunt reports, phase 3).
    """
    template_id = TEMPLATE_IDS.get(message.kind)
    if template_id is None or message.template_id != template_id:
        raise InvalidEmail(f"no template {message.template_id!r} for a {message.kind.value}")
    if message.attachments:
        raise InvalidEmail("e-mails with attachments are not supported yet")
    check_subject(message.subject)
    try:
        body = EMAIL_TEMPLATES.render(f"{template_id}.txt", fields=dict(message.fields))
    except TemplateError as error:
        raise InvalidEmail(f"the body cannot be built: {error}") from None
    if len(body) > MAX_BODY_LENGTH:
        raise InvalidEmail(f"the body is longer than {MAX_BODY_LENGTH} characters")
    return body + "\n"


def check_subject(subject: str) -> None:
    """Raise `InvalidEmail` unless `subject` is one clean line of 1-150 characters, so it can
    neither add a header nor hide text."""
    if not subject or len(subject) > MAX_SUBJECT_LENGTH:
        raise InvalidEmail(f"the subject must be 1-{MAX_SUBJECT_LENGTH} characters long")
    if not is_clean(subject) or subject != clean_text(subject):
        raise InvalidEmail("the subject must be one line without control characters")


def _subject(template_id: str, *, name_field: str, name: str, **fields: FieldValue) -> str:
    """The subject with `name` in `name_field`, cut so the whole subject fits."""
    full = clean_text(name) or "-"
    subject = _render_subject(template_id, {**fields, name_field: full})
    excess = len(subject) - MAX_SUBJECT_LENGTH
    if excess > 0:
        room = len(full) - excess
        if room < 1:
            raise InvalidEmail(f"the subject does not fit in {MAX_SUBJECT_LENGTH} characters")
        subject = _render_subject(template_id, {**fields, name_field: clean_text(full, room)})
    check_subject(subject)
    return subject


def _render_subject(template_id: str, fields: dict[str, FieldValue]) -> str:
    try:
        return EMAIL_TEMPLATES.render(template_id, **fields)
    except TemplateError as error:
        raise InvalidEmail(f"the subject cannot be built: {error}") from None


def _health_fields(request: HealthAlarm) -> dict[str, str]:
    """The fields of a health alarm's e-mail: `count_1`, `count_2`, ... one line each, as the
    events of an alert are."""
    subject_name = _optional(request.subject_name, _SUBJECT_NAME)
    return {
        "alarm_kind": _ALARM_KINDS[request.alarm_kind],
        "state": _ALARM_STATES[request.status],
        "is_resolved": "yes" if request.status == "resolved" else "",
        "subject_line": _alarm_subject(request, subject_name),
        "opened_at": f"{_local(request.opened_at):%Y-%m-%d %H:%M}",
        "notification_no": str(request.notification_no),
        **{
            f"count_{number}": f"{_COUNT_LABELS.get(name, clean_text(name, 40))}: {value}"
            for number, (name, value) in enumerate(sorted(request.counts.items()), start=1)
        },
    }


def _alarm_subject(request: HealthAlarm, subject_name: str | None) -> str:
    """What the alarm is about on one line: the subject, then its name when it has one."""
    subject = clean_text(request.subject, _NAME) or "-"
    return subject if subject_name is None else f"{subject} · {subject_name}"


def _case_fields(request: CaseAlert) -> dict[str, str]:
    content = request.content
    day = _local(request.evaluated_at).date()
    return {
        "offense_id": str(content.offense_id),
        "offense_name": clean_text(request.offense_name, _NAME) or "-",
        "evaluation_no": str(content.evaluation_no),
        "evaluated_at": f"{_local(request.evaluated_at):%Y-%m-%d %H:%M}",
        "level": label("level", content.notify_level),
        "verdict": label("verdict", content.verdict),
        "verdict_is_fp": _is_fp(content.verdict),
        "confidence": label("confidence", content.confidence),
        "summary": clean_text(content.summary_tr, _SUMMARY) or "-",
        **_events(content.urgent_events, day),
        "actions": _actions(content.recommended_actions),
        "data_gaps": _data_gaps(content.data_gaps, day),
        "case_url": content.case_url,
    }


def _group_fields(request: GroupAlert) -> dict[str, str]:
    day = _local(request.evaluated_at).date()
    return {
        "group_id": request.group_id,
        "title": clean_text(request.title, _NAME) or "-",
        "offense_count": str(request.offense_count),
        "evaluation_no": str(request.evaluation_no),
        "evaluated_at": f"{_local(request.evaluated_at):%Y-%m-%d %H:%M}",
        "level": label("level", request.notify_level),
        "verdict": label("verdict", request.verdict),
        "verdict_is_fp": _is_fp(request.verdict),
        "confidence": label("confidence", request.confidence),
        "summary": clean_text(request.summary_tr, _SUMMARY) or "-",
        **_events(request.urgent_events, day),
        "actions": _actions(request.recommended_actions),
        "case_url": request.case_url,
    }


def _is_fp(verdict: CaseVerdict) -> str:
    """Whether the template adds its false-positive line; a field is text."""
    return "yes" if verdict is CaseVerdict.FP else ""


def _events(events: Sequence[UrgentEvent], day: date) -> dict[str, str]:
    """`event_1`, `event_2`, ...: one line per event, by rank."""
    ranked = sorted(events, key=lambda event: event.rank)[:MAX_EVENTS]
    return {f"event_{n}": _event(event, day) for n, event in enumerate(ranked, start=1)}


def _event(event: UrgentEvent, day: date) -> str:
    """The identifiers that find the event in QRadar and why it matters; nothing else of it
    (not its checklist, AQL or evidence ID) goes into an e-mail."""
    return _render_part(
        "parts/event.txt",
        rank=event.rank,
        time=_event_time(event.time, day),
        log_source=clean_text(event.log_source, _LOG_SOURCE) or "-",
        event_name=clean_text(event.event_name, _EVENT_NAME) or "-",
        qid=None if event.qid is None else clean_text(str(event.qid), QID_LENGTH),
        source=_optional(event.source, _ADDRESS),
        destination=_optional(event.destination, _ADDRESS),
        username=_optional(event.username, _USERNAME),
        reason=clean_text(event.reason, _REASON) or "-",
    )


def _actions(actions: Sequence[ActionType]) -> str:
    """The recommended actions, each once, in the order given."""
    unique: list[FieldValue] = list(dict.fromkeys(label("action", action) for action in actions))
    return _render_part("parts/actions.txt", actions=unique)


def _data_gaps(gaps: Sequence[DataGap], day: date) -> str:
    """The first data gaps on one line; empty when there are none."""
    if not gaps:
        return ""
    shown: list[FieldValue] = [
        {
            "source": clean_text(gap.source, _GAP_SOURCE) or "-",
            "reason": label("gap_reason", gap.reason),
            "start": _gap_time(gap.period_start, day),
            "end": _gap_time(gap.period_end, day),
        }
        for gap in gaps[:MAX_GAPS]
    ]
    return _render_part("parts/data_gaps.txt", gaps=shown, more=len(gaps) - len(shown))


def _render_part(template_id: str, **fields: FieldValue) -> str:
    try:
        text = EMAIL_TEMPLATES.render(template_id, **fields)
    except TemplateError as error:
        raise InvalidEmail(f"a field cannot be built: {error}") from None
    if not is_clean(text):
        raise InvalidEmail(f"{template_id} must render one line")
    return text


def _optional(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return clean_text(value, max_length) or None


def _local(value: datetime) -> datetime:
    return value.astimezone(EMAIL_TIME_ZONE)


def _event_time(value: datetime, day: date) -> str:
    """The time alone on the evaluation's day, the date and time on another day."""
    local = _local(value)
    return f"{local:%H:%M:%S}" if local.date() == day else f"{local:%Y-%m-%d %H:%M:%S}"


def _gap_time(value: datetime, day: date) -> str:
    local = _local(value)
    return f"{local:%H:%M}" if local.date() == day else f"{local:%Y-%m-%d %H:%M}"
