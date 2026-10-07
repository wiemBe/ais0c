"""`health_alarms` repository functions and the failure counts of notes and e-mails
(T-032 criteria 4 and 6)."""

from datetime import UTC, datetime, timedelta

import pytest
import storage_payloads as payloads
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, OFFENSE_ID, T0, T1

from ais0c_contracts import CaseSource, EmailKind, Level
from ais0c_storage import DuplicateError, new_uuid7
from ais0c_storage.enums import (
    HealthAlarmKind,
    HealthAlarmStatus,
    NoteStatus,
    NotificationStatus,
)
from ais0c_storage.ids import uuid7_floor
from ais0c_storage.repositories import (
    count_failed_notes,
    count_failed_notifications,
    create_case,
    get_open_health_alarm,
    list_health_alarms,
    list_open_health_alarms,
    mark_health_alarm_notified,
    notification_count,
    open_health_alarm,
    record_note,
    record_notification,
    resolve_health_alarm,
    touch_health_alarm,
)

pytestmark = pytest.mark.anyio

KIND = HealthAlarmKind.LOG_SOURCE_SILENT


async def test_one_open_alarm_per_kind_and_subject(session: AsyncSession) -> None:
    await open_health_alarm(session, kind=KIND, subject="7", now=T0, details={})

    with pytest.raises(DuplicateError):
        await open_health_alarm(session, kind=KIND, subject="7", now=T1, details={})
    # Another subject and another kind are alarms of their own.
    await open_health_alarm(session, kind=KIND, subject="8", now=T1, details={})
    await open_health_alarm(
        session, kind=HealthAlarmKind.WRITE_FAILURES, subject="7", now=T1, details={}
    )

    assert [row.subject for row in await list_open_health_alarms(session, KIND)] == ["7", "8"]
    assert len(await list_open_health_alarms(session)) == 3


async def test_a_resolved_subject_opens_again_as_a_new_alarm(session: AsyncSession) -> None:
    first = await open_health_alarm(session, kind=KIND, subject="7", now=T0, details={})
    resolved = await resolve_health_alarm(session, first.id, now=T1)
    assert (resolved.status, resolved.resolved_at) == (HealthAlarmStatus.RESOLVED, T1)
    assert await get_open_health_alarm(session, KIND, "7") is None

    second = await open_health_alarm(session, kind=KIND, subject="7", now=T1, details={})

    assert second.id != first.id
    history = await list_health_alarms(session, kind=KIND, subject="7")
    assert [row.status for row in history] == [HealthAlarmStatus.RESOLVED, HealthAlarmStatus.OPEN]


async def test_touch_keeps_the_notification_count_and_notify_counts_up(
    session: AsyncSession,
) -> None:
    alarm = await open_health_alarm(
        session, kind=KIND, subject="7", now=T0, details={"silent_minutes": 61}
    )
    assert (notification_count(alarm), alarm.last_notified_at) == (0, None)

    notified = await mark_health_alarm_notified(session, alarm.id, now=T0)
    assert (notification_count(notified), notified.last_notified_at) == (1, T0)
    assert notified.details == {"silent_minutes": 61, "notifications": 1}

    touched = await touch_health_alarm(session, alarm.id, now=T1, details={"silent_minutes": 90})
    assert touched.last_seen_at == T1
    assert touched.details == {"silent_minutes": 90, "notifications": 1}
    assert touched.last_notified_at == T0


def test_uuid7_floor_is_below_every_id_of_that_millisecond() -> None:
    moment = datetime.now(UTC)
    assert uuid7_floor(moment - timedelta(milliseconds=2)) <= new_uuid7()
    assert uuid7_floor(moment + timedelta(minutes=1)) > new_uuid7()


async def test_failed_notes_are_counted_by_their_record_time(session: AsyncSession) -> None:
    await create_case(
        session,
        case_id=CASE_ID,
        source=CaseSource.OFFENSE,
        offense_id=OFFENSE_ID,
        sla_due_at=T1,
        workflow_id=CASE_ID,
        run_id="run-1",
    )
    for marker, status, at in (
        ("aaaaaaaaaaaa", NoteStatus.FAILED, T1),
        ("bbbbbbbbbbbb", NoteStatus.FAILED, T0),
        ("cccccccccccc", NoteStatus.DISABLED, T1),
        ("dddddddddddd", NoteStatus.SKIPPED_DUPLICATE, T1),
        ("eeeeeeeeeeee", NoteStatus.WRITTEN, T1),
    ):
        error = "QRadar returned 503" if status is NoteStatus.FAILED else None
        await record_note(
            session,
            payloads.note_content(run_marker=marker),
            case_id=CASE_ID,
            status=status,
            written_at=at,
            error=error,
        )

    assert await count_failed_notes(session, since=T0) == 2
    assert await count_failed_notes(session, since=T1) == 1
    assert await count_failed_notes(session, since=T1 + timedelta(seconds=1)) == 0


async def test_failed_and_rejected_emails_count_and_disabled_ones_do_not(
    session: AsyncSession,
) -> None:
    before = datetime.now(UTC) - timedelta(minutes=1)
    for number, status in enumerate(
        (
            NotificationStatus.FAILED,
            NotificationStatus.REJECTED,
            NotificationStatus.DISABLED,
            NotificationStatus.DISABLED,
            NotificationStatus.SENT,
        )
    ):
        await record_notification(
            session,
            payloads.email_message(idempotency_key=f"case_alert:case-1:{number}"),
            status=status,
            level=Level.HIGH,
            case_id="case-1",
            sent_at=T0 if status is NotificationStatus.SENT else None,
            error="relay down"
            if status in {NotificationStatus.FAILED, NotificationStatus.REJECTED}
            else None,
        )

    assert await count_failed_notifications(session, since=before) == 2
    assert (
        await count_failed_notifications(session, since=datetime.now(UTC) + timedelta(hours=1)) == 0
    )


async def test_a_health_alarm_email_has_no_level_and_no_subject_id(
    session: AsyncSession,
) -> None:
    message = payloads.email_message(
        idempotency_key="health_alarm:1:open:1", kind=EmailKind.HEALTH_ALARM.value
    )

    row = await record_notification(
        session, message, status=NotificationStatus.FAILED, error="no executor"
    )
    assert (row.kind, row.level, row.case_id) == (EmailKind.HEALTH_ALARM, None, None)

    with pytest.raises(ValueError, match="notify level"):
        await record_notification(
            session,
            message.model_copy(update={"idempotency_key": "health_alarm:1:open:2"}),
            status=NotificationStatus.FAILED,
            level=Level.HIGH,
            error="no executor",
        )
