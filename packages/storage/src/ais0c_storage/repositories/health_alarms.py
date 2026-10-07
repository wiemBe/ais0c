"""`health_alarms`: the platform's own alarms (T-032, decisions T-23, T-68).

One `open` row per (kind, subject). A check opens it, keeps `last_seen_at` and `details` current
while the condition holds and resolves it when the condition is gone; the notification state is
`last_notified_at` and the count of notifications in `details.notifications`.
"""

import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.enums import HealthAlarmKind, HealthAlarmStatus
from ais0c_storage.models import HealthAlarmRow
from ais0c_storage.repositories._common import fetch_all, fetch_one, insert_new, update_one

# `details` key: how many notifications (opened, reminders, resolved) the alarm sent.
NOTIFICATIONS_KEY = "notifications"


def notification_count(alarm: HealthAlarmRow) -> int:
    """How many notifications went out for `alarm`."""
    details = alarm.details
    count = details.get(NOTIFICATIONS_KEY) if isinstance(details, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


async def get_health_alarm(session: AsyncSession, alarm_id: uuid.UUID) -> HealthAlarmRow | None:
    return await session.get(HealthAlarmRow, alarm_id, populate_existing=True)


async def get_open_health_alarm(
    session: AsyncSession, kind: HealthAlarmKind, subject: str
) -> HealthAlarmRow | None:
    statement = select(HealthAlarmRow).where(
        HealthAlarmRow.kind == kind,
        HealthAlarmRow.subject == subject,
        HealthAlarmRow.status == HealthAlarmStatus.OPEN,
    )
    return await fetch_one(session, statement)


async def list_open_health_alarms(
    session: AsyncSession, kind: HealthAlarmKind | None = None
) -> list[HealthAlarmRow]:
    """The open alarms, optionally of one kind, oldest first."""
    statement = select(HealthAlarmRow).where(HealthAlarmRow.status == HealthAlarmStatus.OPEN)
    if kind is not None:
        statement = statement.where(HealthAlarmRow.kind == kind)
    return await fetch_all(session, statement.order_by(HealthAlarmRow.opened_at, HealthAlarmRow.id))


async def list_health_alarms(
    session: AsyncSession, *, kind: HealthAlarmKind | None = None, subject: str | None = None
) -> list[HealthAlarmRow]:
    """Every alarm, open or resolved, oldest first."""
    statement = select(HealthAlarmRow)
    if kind is not None:
        statement = statement.where(HealthAlarmRow.kind == kind)
    if subject is not None:
        statement = statement.where(HealthAlarmRow.subject == subject)
    return await fetch_all(session, statement.order_by(HealthAlarmRow.opened_at, HealthAlarmRow.id))


async def open_health_alarm(
    session: AsyncSession,
    *,
    kind: HealthAlarmKind,
    subject: str,
    now: datetime,
    details: dict[str, JsonValue],
) -> HealthAlarmRow:
    """Open an alarm. Raises `DuplicateError` when the subject already has an open one."""
    values = dict(
        kind=kind,
        subject=subject,
        status=HealthAlarmStatus.OPEN,
        opened_at=now,
        last_seen_at=now,
        details=details,
    )
    return await insert_new(session, HealthAlarmRow, values, f"open {kind.value} alarm {subject!r}")


async def touch_health_alarm(
    session: AsyncSession, alarm_id: uuid.UUID, *, now: datetime, details: dict[str, JsonValue]
) -> HealthAlarmRow:
    """The condition still holds: record when it was seen and its current `details`. The
    notification count is kept."""
    current = await get_health_alarm(session, alarm_id)
    count = 0 if current is None else notification_count(current)
    statement = (
        update(HealthAlarmRow)
        .where(HealthAlarmRow.id == alarm_id)
        .values(last_seen_at=now, details={**details, NOTIFICATIONS_KEY: count})
    )
    return await update_one(session, statement, HealthAlarmRow, f"health alarm {alarm_id}")


async def resolve_health_alarm(
    session: AsyncSession, alarm_id: uuid.UUID, *, now: datetime
) -> HealthAlarmRow:
    statement = (
        update(HealthAlarmRow)
        .where(HealthAlarmRow.id == alarm_id)
        .values(status=HealthAlarmStatus.RESOLVED, resolved_at=now)
    )
    return await update_one(session, statement, HealthAlarmRow, f"health alarm {alarm_id}")


async def mark_health_alarm_notified(
    session: AsyncSession, alarm_id: uuid.UUID, *, now: datetime
) -> HealthAlarmRow:
    """A notification of the alarm went out: `last_notified_at` is now and the count goes up."""
    current = await get_health_alarm(session, alarm_id)
    count = 0 if current is None else notification_count(current)
    base = current.details if current is not None and isinstance(current.details, dict) else {}
    statement = (
        update(HealthAlarmRow)
        .where(HealthAlarmRow.id == alarm_id)
        .values(last_notified_at=now, details={**base, NOTIFICATIONS_KEY: count + 1})
    )
    return await update_one(session, statement, HealthAlarmRow, f"health alarm {alarm_id}")
