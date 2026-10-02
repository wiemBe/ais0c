"""Repository functions of `notes_written` and `notifications` (criterion 6)."""

import pytest
import storage_payloads as payloads
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, OFFENSE_ID, T0, T1

from ais0c_contracts import CaseSource, EmailKind
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
        session, message, status=NotificationStatus.FAILED, case_id=CASE_ID
    )
    assert (failed.kind, failed.recipients, failed.subject, failed.sent_at) == (
        EmailKind.CASE_ALERT,
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
            session, payloads.email_message(), status=NotificationStatus.SENT, case_id=CASE_ID
        )
    with pytest.raises(ValueError, match="sent_at"):
        await record_notification(
            session,
            payloads.email_message(),
            status=NotificationStatus.REJECTED,
            case_id=CASE_ID,
            sent_at=T1,
        )


async def test_list_emails_of_a_case_a_group_or_a_hunt(session: AsyncSession) -> None:
    await record_notification(
        session,
        payloads.email_message(idempotency_key="case-12345:1"),
        status=NotificationStatus.SENT,
        case_id=CASE_ID,
        sent_at=T0,
    )
    await record_notification(
        session,
        payloads.email_message(idempotency_key="case-12345:2"),
        status=NotificationStatus.REJECTED,
        case_id=CASE_ID,
    )
    await record_notification(
        session,
        payloads.email_message(idempotency_key="group-G-1", kind="group_alert"),
        status=NotificationStatus.SENT,
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
