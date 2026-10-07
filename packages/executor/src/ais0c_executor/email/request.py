"""What an alert e-mail is sent from, and what sending it came to (architecture §9).

Two requests make the two kinds of alert (`EmailKind`):

- `CaseAlert`: a case whose notify level is high or critical. Its body carries the fields of the
  case's QRadar note (`NoteContent`: summary, urgent events, recommended actions, data gaps and
  the link to the platform) and the offense's name.
- `GroupAlert`: a group of offenses that went into storm state and was evaluated as one case
  (architecture §9, "Offense gruplama ve fırtına koruması").

A third request is not an alert about a case:

- `HealthAlarm`: the platform's own health alarm (T-23, T-68, T-032), opened, reminded or
  resolved. It is not an AI output, so the kill switch does not hold it back (`EmailSender`).

Each request names its e-mail's idempotency key, the same on every attempt:

- case alert: `case_alert:<case_id>:<evaluation_no>`, at most one e-mail per evaluation;
- group alert: `group_alert:<group_id>:<evaluation_no>`, at most one e-mail per evaluation;
- health alarm: `health_alarm:<alarm_id>:<status>:<notification_no>`, one e-mail per
  notification of the alarm.

Identifiers and the case link are platform values, checked here so they cannot change the
e-mail's layout. Names and texts come from QRadar or a model and may carry text from a log; the
renderer cleans and cuts them.
"""

import re
import uuid
from enum import StrEnum
from typing import Annotated, Final, Literal, Self, cast

import pydantic_core
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from ais0c_contracts import (
    ActionType,
    CaseVerdict,
    Confidence,
    EmailKind,
    Level,
    NoteContent,
    UrgentEvent,
    UtcDatetime,
)
from ais0c_executor.common import (
    check_case_id,
    check_case_url,
    check_evaluation_no,
    check_group_id,
    check_offense_id,
)
from ais0c_executor.email.errors import InvalidEmail

# An offense's description or a group's title as it arrives; the e-mail shows a shorter cut.
MAX_NAME_LENGTH: Final = 2000
MAX_SUMMARY_LENGTH: Final = 400


class CaseAlert(BaseModel):
    """The e-mail of a case whose notify level is high or critical."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["case_alert"] = "case_alert"
    case_id: str
    offense_name: Annotated[str, StringConstraints(max_length=MAX_NAME_LENGTH)]
    """The offense's description in QRadar."""
    evaluated_at: UtcDatetime
    """When the evaluation was decided; the time the e-mail shows."""
    content: NoteContent
    """The fields of the evaluation's QRadar note."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        content = self.content
        check_case_id(self.case_id)
        if content.group_id is not None:
            raise ValueError(
                "an offense evaluated within its group is e-mailed with the group's alert"
            )
        check_offense_id(content.offense_id)
        check_evaluation_no(content.evaluation_no)
        check_case_url(content.case_url)
        return self

    @property
    def email_kind(self) -> EmailKind:
        return EmailKind.CASE_ALERT

    @property
    def level(self) -> Level:
        return self.content.notify_level

    @property
    def evaluation_no(self) -> int:
        return self.content.evaluation_no

    @property
    def idempotency_key(self) -> str:
        return f"case_alert:{self.case_id}:{self.content.evaluation_no}"


class GroupAlert(BaseModel):
    """A group's alert for an evaluation whose level rises above its sent alerts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["group_alert"] = "group_alert"
    case_id: str
    """The group's evaluation case (`offense_groups.case_id`)."""
    group_id: str
    title: Annotated[str, StringConstraints(max_length=MAX_NAME_LENGTH)]
    """What the group's offenses share: the offenses' description in QRadar or the rules'
    names."""
    offense_count: Annotated[int, Field(ge=1)]
    evaluation_no: int
    evaluated_at: UtcDatetime
    """When the group's evaluation was decided; the time the e-mail shows."""
    verdict: CaseVerdict
    confidence: Confidence
    notify_level: Level
    summary_tr: Annotated[str, StringConstraints(max_length=MAX_SUMMARY_LENGTH)]
    urgent_events: Annotated[list[UrgentEvent], Field(max_length=5)]
    recommended_actions: list[ActionType]
    case_url: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_case_id(self.case_id)
        check_group_id(self.group_id)
        check_evaluation_no(self.evaluation_no)
        check_case_url(self.case_url)
        return self

    @property
    def email_kind(self) -> EmailKind:
        return EmailKind.GROUP_ALERT

    @property
    def level(self) -> Level:
        return self.notify_level

    @property
    def idempotency_key(self) -> str:
        return f"group_alert:{self.group_id}:{self.evaluation_no}"


# What a health alarm can be about and its states; `ais0c_storage.enums.HealthAlarmKind` has the
# same kinds (a test in this package keeps them together).
type HealthAlarmKindName = Literal[
    "intake_stopped", "log_source_silent", "write_failures", "executor_absent"
]
type HealthAlarmState = Literal["open", "reminder", "resolved"]
MAX_SUBJECT_LENGTH: Final = 200
MAX_COUNTS: Final = 6
_COUNT_NAME: Final = re.compile(r"[a-z][a-z_]{0,31}")


class HealthAlarm(BaseModel):
    """One notification of a health alarm: it opened, it is still open, or it resolved.

    `subject` is what the alarm is about (a log source's ID, `intake`, `qradar_unreachable`, the
    queue's name) and `subject_name` its display name when it has one, such as a log source's
    name: QRadar's text, cleaned by the renderer. `counts` are the numbers the message shows,
    each under a short lower-case name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["health_alarm"] = "health_alarm"
    alarm_id: str
    """The alarm's row in `health_alarms` (a UUID)."""
    alarm_kind: HealthAlarmKindName
    subject: Annotated[str, StringConstraints(min_length=1, max_length=MAX_SUBJECT_LENGTH)]
    subject_name: Annotated[str, StringConstraints(max_length=MAX_NAME_LENGTH)] | None = None
    status: HealthAlarmState
    notification_no: Annotated[int, Field(ge=1)]
    """How many notifications the alarm has sent, this one included."""
    opened_at: UtcDatetime
    counts: dict[str, int] = Field(default_factory=dict[str, int])

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            if str(uuid.UUID(self.alarm_id)) != self.alarm_id:
                raise ValueError
        except ValueError:
            raise ValueError("alarm_id must be a UUID in its canonical form") from None
        if len(self.counts) > MAX_COUNTS:
            raise ValueError(f"at most {MAX_COUNTS} counts")
        for name in self.counts:
            if not _COUNT_NAME.fullmatch(name):
                raise ValueError("count names are lower-case letters and underscores")
        return self

    @property
    def email_kind(self) -> EmailKind:
        return EmailKind.HEALTH_ALARM

    @property
    def idempotency_key(self) -> str:
        return f"health_alarm:{self.alarm_id}:{self.status}:{self.notification_no}"


# The alerts of a case or a group: they carry a notify level and follow the level rule.
type AlertRequest = CaseAlert | GroupAlert
type EmailRequest = Annotated[CaseAlert | GroupAlert | HealthAlarm, Field(discriminator="kind")]

_REQUEST: Final[TypeAdapter[EmailRequest]] = TypeAdapter(EmailRequest)


def validated[R: CaseAlert | GroupAlert | HealthAlarm](request: R) -> R:
    """`request` validated again from its JSON form, so one built with `model_construct()` is
    checked too. Raises `InvalidEmail`, naming the fields but not their values."""
    try:
        return cast(R, _REQUEST.validate_json(pydantic_core.to_json(request)))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'request'}: {item['msg']}"
            for item in error.errors(include_url=False, include_input=False, include_context=False)
        )
        raise InvalidEmail(f"invalid e-mail request: {problems}") from None


class EmailResult(StrEnum):
    """What sending an alert came to.

    - `sent`: the relay took the e-mail; `notifications` records it as `sent` and `audit_log`
      as `email.send`.
    - `already_sent`: an earlier attempt with the same idempotency key sent it; nothing was
      sent again.
    - `not_needed`: the level is below high, or a re-evaluation did not raise it above the
      levels already e-mailed about the case. Nothing was sent or recorded.
    - `rejected`: a recipient is outside the allowed company domains. Nothing was sent;
      `notifications` records it as `rejected`, with the reason in `error`, and `audit_log` as
      `email.reject`.
    - `writes_disabled`: the kill switch was off (T-23). Nothing was sent; `notifications`
      records it as `disabled`, which is not a failure (T-37), and a later attempt with writes
      on sends it.
    - `failed`: the relay did not take the e-mail, or the recipient list is empty;
      `notifications` records it as `failed`, with the reason in `error`.
    """

    SENT = "sent"
    ALREADY_SENT = "already_sent"
    NOT_NEEDED = "not_needed"
    REJECTED = "rejected"
    WRITES_DISABLED = "writes_disabled"
    FAILED = "failed"


class EmailOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: EmailResult
    kind: EmailKind
    idempotency_key: str
    error: str | None = None
    """Why the e-mail was not sent, for `rejected`, `writes_disabled` and `failed`."""
