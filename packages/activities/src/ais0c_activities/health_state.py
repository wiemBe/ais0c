"""The state of the platform's health alarms (T-032, decisions T-23, T-68 (4)).

A check looks at one thing and hands `reconcile` what is wrong *now*, as `Finding`s of one alarm
kind. `reconcile` brings `health_alarms` in line and returns the notifications that are due:

- A finding with no open alarm opens one. The alarm is announced once its `grace` has passed
  since it opened (no grace for most kinds; "the executor has been absent for N minutes" is the
  grace of its kind): a notification with state `open`.
- A finding whose alarm is open keeps it open and current. Once it was announced it is reminded
  every `renotify`: state `reminder`. It is never announced twice in between.
- An open alarm with no finding any more is resolved, and a "resolved" notification is due if it
  was ever announced. An alarm that cleared within its grace is resolved without a word.
- The same subject alarming again after it was resolved is a new alarm and is announced again.

The notification is not marked as sent here: the workflow sends it on its channels and then calls
`mark_alarm_notified`. A notification that was not marked comes up again at the next run with the
same number, so its e-mail has the same idempotency key and goes out once.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_executor.email import HealthAlarm
from ais0c_storage import DuplicateError
from ais0c_storage.enums import HealthAlarmKind
from ais0c_storage.models import HealthAlarmRow
from ais0c_storage.repositories import (
    get_open_health_alarm,
    list_open_health_alarms,
    notification_count,
    open_health_alarm,
    resolve_health_alarm,
    touch_health_alarm,
)

# `details` keys: the numbers the notification shows, and the subject's display name.
COUNTS_KEY: Final = "counts"
SUBJECT_NAME_KEY: Final = "subject_name"


@dataclass(frozen=True)
class Finding:
    """One thing that is wrong now."""

    subject: str
    """What it is about: a log source's ID, `intake`, `qradar_unreachable`, a queue's name."""
    counts: dict[str, int] = field(default_factory=dict[str, int])
    """The numbers the notification shows, each under a short lower-case name."""
    subject_name: str | None = None
    """The subject's display name, for a log source its name in QRadar."""
    grace: timedelta = timedelta(0)
    """How long after the alarm opened it is announced."""

    def details(self) -> dict[str, JsonValue]:
        details: dict[str, JsonValue] = {COUNTS_KEY: dict(self.counts)}
        if self.subject_name is not None:
            details[SUBJECT_NAME_KEY] = self.subject_name
        return details


async def reconcile(
    session: AsyncSession,
    kind: HealthAlarmKind,
    findings: Sequence[Finding],
    *,
    now: datetime,
    renotify: timedelta,
    unchecked: Collection[str] = (),
) -> list[HealthAlarm]:
    """Bring the open alarms of `kind` in line with `findings`; return the notifications due.

    An open alarm of a subject in `unchecked` is left alone: the check could not look at it this
    time (QRadar unreachable, say), so it is neither kept nor resolved. The caller owns the
    transaction.
    """
    due: list[HealthAlarm] = []
    found = {finding.subject for finding in findings}
    for finding in findings:
        alarm = await get_open_health_alarm(session, kind, finding.subject)
        if alarm is None:
            try:
                alarm = await open_health_alarm(
                    session,
                    kind=kind,
                    subject=finding.subject,
                    now=now,
                    details=finding.details(),
                )
            except DuplicateError:  # opened by a run that overlapped this one
                continue
        else:
            alarm = await touch_health_alarm(session, alarm.id, now=now, details=finding.details())
        state = _due_state(alarm, finding.grace, now=now, renotify=renotify)
        if state is not None:
            due.append(_notice(alarm, state))
    for alarm in await list_open_health_alarms(session, kind):
        if alarm.subject in found or alarm.subject in unchecked:
            continue
        resolved = await resolve_health_alarm(session, alarm.id, now=now)
        if resolved.last_notified_at is not None:
            due.append(_notice(resolved, "resolved"))
    return due


def _due_state(
    alarm: HealthAlarmRow, grace: timedelta, *, now: datetime, renotify: timedelta
) -> str | None:
    if alarm.last_notified_at is None:
        return "open" if now - alarm.opened_at >= grace else None
    return "reminder" if now - alarm.last_notified_at >= renotify else None


def _notice(alarm: HealthAlarmRow, state: str) -> HealthAlarm:
    """The notification of `alarm` in `state`: the next in its count."""
    details = alarm.details if isinstance(alarm.details, dict) else {}
    raw_counts = details.get(COUNTS_KEY)
    counts = (
        {name: value for name, value in raw_counts.items() if isinstance(value, int)}
        if isinstance(raw_counts, dict)
        else {}
    )
    name = details.get(SUBJECT_NAME_KEY)
    return HealthAlarm.model_validate(
        {
            "alarm_id": str(alarm.id),
            "alarm_kind": alarm.kind.value,
            "subject": alarm.subject,
            "subject_name": name if isinstance(name, str) else None,
            "status": state,
            "notification_no": notification_count(alarm) + 1,
            "opened_at": alarm.opened_at,
            "counts": counts,
        }
    )
