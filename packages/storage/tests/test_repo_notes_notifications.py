"""Repository functions of `notes_written` and `notifications` (criterion 6), with the
`disabled` status and `notifications.error` of contracts v0.4 (T-041 criteria 3-5)."""

import pytest
import storage_payloads as payloads
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, OFFENSE_ID, T0, T1

from ais0c_contracts import CaseSource, EmailKind, Level
from ais0c_storage.enums import NoteStatus, NotificationStatus
from ais0c_storage.errors import NotFoundError
from ais0c_storage.repositories import (
    create_case,
    get_note,
    get_notification,
    list_notes,
    list_notifications,
    record_note,
    record_notification,
    update_note_status,
    update_notification_status,
)

pytestmark = pytest.mark.anyio


async def open_case(session: AsyncSession) -> None:
    await create_case(
        session,
        case_id=CASE_ID,
        source=CaseSource.OFFENSE,
        offense_id=OFFENSE_ID,
        sla_due_at=T1,
        workflow_id=CASE_ID,
        run_id="run-1",
    )


async def test_a_failed_note_can_be_written_on_retry(session: AsyncSession) -> None:
    await open_case(session)
    note = payloads.note_content(run_marker="7f3a9c")

    failed = await record_note(
        session,
        note,
        case_id=CASE_ID,
        status=NoteStatus.FAILED,
        written_at=T0,
        error="QRadar returned 503",
    )
    assert (failed.offense_id, failed.evaluation_no, failed.run_marker) == (
        OFFENSE_ID,
        1,
        "7f3a9c",
    )

    written = await update_note_status(
        session,
        offense_id=OFFENSE_ID,
        run_marker="7f3a9c",
        status=NoteStatus.WRITTEN,
        written_at=T1,
    )

    assert (written.id, written.status, written.error, written.written_at) == (
        failed.id,
        NoteStatus.WRITTEN,
        None,
        T1,
    )
    stored = await get_note(session, offense_id=OFFENSE_ID, run_marker="7f3a9c")
    assert stored is not None
    assert stored.status is NoteStatus.WRITTEN
    assert await get_note(session, offense_id=OFFENSE_ID, run_marker="000000") is None
    with pytest.raises(NotFoundError):
        await update_note_status(
            session,
            offense_id=OFFENSE_ID,
            run_marker="000000",
            status=NoteStatus.WRITTEN,
            written_at=T1,
        )


async def test_notes_of_a_case_by_evaluation(session: AsyncSession) -> None:
    await open_case(session)
    for evaluation_no, marker in ((2, "bbbbbb"), (1, "aaaaaa")):
        await record_note(
            session,
            payloads.note_content(run_marker=marker, evaluation_no=evaluation_no),
            case_id=CASE_ID,
            status=NoteStatus.SKIPPED_DUPLICATE,
            written_at=T1,
        )

    notes = await list_notes(session, CASE_ID)

    assert [(note.evaluation_no, note.run_marker) for note in notes] == [
        (1, "aaaaaa"),
        (2, "bbbbbb"),
    ]


async def test_a_failed_email_can_be_sent_on_retry(session: AsyncSession) -> None:
    message = payloads.email_message(idempotency_key="case-12345:1")

    failed = await record_notification(
        session, message, status=NotificationStatus.FAILED, level=Level.HIGH, case_id=CASE_ID
    )
    assert (failed.kind, failed.level, failed.recipients, failed.subject, failed.sent_at) == (
        EmailKind.CASE_ALERT,
        Level.HIGH,
        ["soc-operators@example.com"],
        message.subject,
        None,
    )

    sent = await update_notification_status(
        session, "case-12345:1", status=NotificationStatus.SENT, sent_at=T1
    )

    assert (sent.id, sent.status, sent.sent_at) == (failed.id, NotificationStatus.SENT, T1)
    stored = await get_notification(session, "case-12345:1")
    assert stored is not None
    assert stored.status is NotificationStatus.SENT
    with pytest.raises(NotFoundError):
        await update_notification_status(session, "unknown", status=NotificationStatus.FAILED)


async def test_email_record_rules(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="needs the ID"):
        await record_notification(
            session,
            payloads.email_message(kind="group_alert"),
            status=NotificationStatus.FAILED,
            case_id=CASE_ID,
        )
    with pytest.raises(ValueError, match="sent_at"):
        await record_notification(
            session,
            payloads.email_message(),
            status=NotificationStatus.SENT,
            level=Level.HIGH,
            case_id=CASE_ID,
        )
    with pytest.raises(ValueError, match="sent_at"):
        await record_notification(
            session,
            payloads.email_message(),
            status=NotificationStatus.REJECTED,
            level=Level.HIGH,
            case_id=CASE_ID,
            sent_at=T1,
        )


@pytest.mark.parametrize(
    ("kind", "level"),
    [("case_alert", None), ("group_alert", None), ("hunt_report", Level.HIGH)],
    ids=["case-alert-without-level", "group-alert-without-level", "hunt-report-with-level"],
)
async def test_only_an_alert_has_a_level_and_every_alert_has_one(
    session: AsyncSession, kind: str, level: Level | None
) -> None:
    with pytest.raises(ValueError, match="notify level"):
        await record_notification(
            session,
            payloads.email_message(kind=kind),
            status=NotificationStatus.FAILED,
            level=level,
            case_id=CASE_ID,
            group_id="G-1",
            hunt_id="hunt-1",
        )


async def test_an_alert_keeps_its_level_and_a_hunt_report_has_none(
    session: AsyncSession,
) -> None:
    critical = await record_notification(
        session,
        payloads.email_message(idempotency_key="case-12345:3"),
        status=NotificationStatus.SENT,
        level=Level.CRITICAL,
        case_id=CASE_ID,
        sent_at=T1,
    )
    report = await record_notification(
        session,
        payloads.email_message(idempotency_key="hunt-1", kind="hunt_report"),
        status=NotificationStatus.SENT,
        hunt_id="hunt-1",
        sent_at=T1,
    )

    stored = await get_notification(session, "case-12345:3")
    assert stored is not None
    assert (critical.level, stored.level, report.level) == (Level.CRITICAL, Level.CRITICAL, None)


async def test_list_emails_of_a_case_a_group_or_a_hunt(session: AsyncSession) -> None:
    await record_notification(
        session,
        payloads.email_message(idempotency_key="case-12345:1"),
        status=NotificationStatus.SENT,
        level=Level.HIGH,
        case_id=CASE_ID,
        sent_at=T0,
    )
    await record_notification(
        session,
        payloads.email_message(idempotency_key="case-12345:2"),
        status=NotificationStatus.REJECTED,
        level=Level.CRITICAL,
        case_id=CASE_ID,
    )
    await record_notification(
        session,
        payloads.email_message(idempotency_key="group-G-1", kind="group_alert"),
        status=NotificationStatus.SENT,
        level=Level.HIGH,
        group_id="G-1",
        sent_at=T1,
    )

    by_case = await list_notifications(session, case_id=CASE_ID)
    by_group = await list_notifications(session, group_id="G-1")

    assert [row.idempotency_key for row in by_case] == ["case-12345:1", "case-12345:2"]
    assert [row.idempotency_key for row in by_group] == ["group-G-1"]
    assert await list_notifications(session, hunt_id="hunt-1") == []
    with pytest.raises(ValueError, match="required"):
        await list_notifications(session)


async def test_a_note_the_kill_switch_held_back_is_disabled_and_written_later(
    session: AsyncSession,
) -> None:
    await open_case(session)

    disabled = await record_note(
        session,
        payloads.note_content(run_marker="7f3a9c"),
        case_id=CASE_ID,
        status=NoteStatus.DISABLED,
        written_at=T0,
    )
    assert (disabled.status, disabled.error) == (NoteStatus.DISABLED, None)

    written = await update_note_status(
        session,
        offense_id=OFFENSE_ID,
        run_marker="7f3a9c",
        status=NoteStatus.WRITTEN,
        written_at=T1,
    )

    assert (written.id, written.status, written.error, written.written_at) == (
        disabled.id,
        NoteStatus.WRITTEN,
        None,
        T1,
    )


async def test_an_email_the_kill_switch_held_back_is_disabled_and_sent_later(
    session: AsyncSession,
) -> None:
    message = payloads.email_message(idempotency_key="case_alert:case-12345:1")

    disabled = await record_notification(
        session, message, status=NotificationStatus.DISABLED, level=Level.HIGH, case_id=CASE_ID
    )
    assert (disabled.status, disabled.error, disabled.sent_at) == (
        NotificationStatus.DISABLED,
        None,
        None,
    )
    assert (disabled.recipients, disabled.subject, disabled.level) == (
        ["soc-operators@example.com"],
        message.subject,
        Level.HIGH,
    )

    sent = await update_notification_status(
        session, "case_alert:case-12345:1", status=NotificationStatus.SENT, sent_at=T1
    )

    assert (sent.id, sent.status, sent.sent_at, sent.error) == (
        disabled.id,
        NotificationStatus.SENT,
        T1,
        None,
    )


async def test_a_failed_or_rejected_email_records_why(session: AsyncSession) -> None:
    failed = await record_notification(
        session,
        payloads.email_message(idempotency_key="case_alert:case-12345:1"),
        status=NotificationStatus.FAILED,
        level=Level.HIGH,
        case_id=CASE_ID,
        error="send: the relay answered 451 4.3.0 later",
    )
    rejected = await record_notification(
        session,
        payloads.email_message(idempotency_key="case_alert:case-12345:2"),
        status=NotificationStatus.REJECTED,
        level=Level.CRITICAL,
        case_id=CASE_ID,
        error="1 of 3 recipients are not plain addresses in the allowed domains",
    )
    assert (failed.error, rejected.error) == (
        "send: the relay answered 451 4.3.0 later",
        "1 of 3 recipients are not plain addresses in the allowed domains",
    )

    # A later attempt's outcome replaces the reason.
    again = await update_notification_status(
        session,
        "case_alert:case-12345:1",
        status=NotificationStatus.FAILED,
        error="connect: ConnectionRefusedError",
    )
    assert again.error == "connect: ConnectionRefusedError"
    sent = await update_notification_status(
        session, "case_alert:case-12345:1", status=NotificationStatus.SENT, sent_at=T1
    )
    assert (sent.status, sent.error) == (NotificationStatus.SENT, None)
    stored = await get_notification(session, "case_alert:case-12345:2")
    assert stored is not None
    assert stored.error == "1 of 3 recipients are not plain addresses in the allowed domains"


@pytest.mark.parametrize("status", [NotificationStatus.SENT, NotificationStatus.DISABLED])
async def test_only_a_failed_or_rejected_email_has_an_error(
    session: AsyncSession, status: NotificationStatus
) -> None:
    sent_at = T1 if status is NotificationStatus.SENT else None
    with pytest.raises(ValueError, match="only a failed or rejected e-mail has an error"):
        await record_notification(
            session,
            payloads.email_message(idempotency_key="case_alert:case-12345:1"),
            status=status,
            level=Level.HIGH,
            case_id=CASE_ID,
            sent_at=sent_at,
            error="the kill switch is off",
        )
    await record_notification(
        session,
        payloads.email_message(idempotency_key="case_alert:case-12345:1"),
        status=NotificationStatus.FAILED,
        level=Level.HIGH,
        case_id=CASE_ID,
        error="send: the relay answered 451 4.3.0 later",
    )
    with pytest.raises(ValueError, match="only a failed or rejected e-mail has an error"):
        await update_notification_status(
            session, "case_alert:case-12345:1", status=status, sent_at=sent_at, error="x"
        )
    stored = await get_notification(session, "case_alert:case-12345:1")
    assert stored is not None
    assert (stored.status, stored.error) == (
        NotificationStatus.FAILED,
        "send: the relay answered 451 4.3.0 later",
    )
