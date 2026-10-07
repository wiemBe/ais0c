"""The record of a note or e-mail the case gave up on (T-032 criterion 9, T-59 (7)): `failed`
with the error `executor_unavailable`, nothing changed where a row is already there."""

from datetime import UTC, datetime, timedelta

import pytest
from activity_db import start_case_row

from ais0c_activities import AbandonedCall, ExecutorFailureActivities, SessionFactory
from ais0c_contracts import CaseSource, CatalogMode, EmailKind, EmailMessage, Level
from ais0c_storage import NotificationStatus
from ais0c_storage.enums import NoteStatus, OffenseStatus
from ais0c_storage.repositories import (
    add_offense_seen,
    create_case,
    create_offense_group,
    get_notification,
    list_notes,
    record_note_failure,
    record_notification,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
CASE = "case-101"
GROUP_CASE = "group-G-0123456789ab-20261007T090000Z"
GROUP = "G-0123456789ab-20261007T090000Z"


def activities(sessions: SessionFactory) -> ExecutorFailureActivities:
    return ExecutorFailureActivities(sessions=sessions, clock=lambda: NOW)


async def case(sessions: SessionFactory, offense_id: int = 101) -> None:
    async with sessions.begin() as session:
        await add_offense_seen(
            session,
            offense_id=offense_id,
            first_seen_at=NOW,
            last_updated_at=NOW,
            description="AIS0C LAB - test",
            rule_ids=[100201],
            catalog_mode=CatalogMode.ANALYZE,
            pre_priority=3,
            status=OffenseStatus.PENDING,
        )
    await start_case_row(sessions, offense_id)


def note_call(**changes: object) -> AbandonedCall:
    values: dict[str, object] = {
        "kind": "note",
        "case_id": CASE,
        "evaluation_no": 2,
        "offense_id": 101,
        "run_marker": "0123456789ab",
    }
    return AbandonedCall.model_validate(values | changes)


def email_call(**changes: object) -> AbandonedCall:
    values: dict[str, object] = {
        "kind": "email",
        "case_id": CASE,
        "evaluation_no": 2,
        "email_kind": "case_alert",
        "level": "critical",
        "idempotency_key": f"case_alert:{CASE}:2",
    }
    return AbandonedCall.model_validate(values | changes)


async def test_a_given_up_note_is_recorded_as_failed(sessions: SessionFactory) -> None:
    await case(sessions)

    assert await activities(sessions).record_executor_failure(note_call()) is True

    async with sessions() as session:
        [row] = await list_notes(session, CASE)
    assert (row.offense_id, row.evaluation_no, row.run_marker) == (101, 2, "0123456789ab")
    assert (row.status, row.error, row.written_at) == (
        NoteStatus.FAILED,
        "executor_unavailable",
        NOW,
    )


async def test_a_note_the_executor_already_recorded_is_left_as_it_is(
    sessions: SessionFactory,
) -> None:
    await case(sessions)
    async with sessions.begin() as session:
        await record_note_failure(
            session,
            case_id=CASE,
            offense_id=101,
            evaluation_no=2,
            run_marker="0123456789ab",
            written_at=datetime(2026, 10, 7, 11, 0, tzinfo=UTC),
            error="QRadar answered HTTP 503",
        )

    assert await activities(sessions).record_executor_failure(note_call()) is False

    async with sessions() as session:
        [row] = await list_notes(session, CASE)
    assert (row.error, row.written_at) == (
        "QRadar answered HTTP 503",
        datetime(2026, 10, 7, 11, 0, tzinfo=UTC),
    )


async def test_a_given_up_case_alert_is_recorded_as_a_failed_notification(
    sessions: SessionFactory,
) -> None:
    await case(sessions)

    assert await activities(sessions).record_executor_failure(email_call()) is True

    async with sessions() as session:
        row = await get_notification(session, f"case_alert:{CASE}:2")
    assert row is not None
    assert (row.kind, row.level, row.case_id, row.group_id) == (
        EmailKind.CASE_ALERT,
        Level.CRITICAL,
        CASE,
        None,
    )
    assert (row.status, row.error, row.sent_at, row.recipients) == (
        NotificationStatus.FAILED,
        "executor_unavailable",
        None,
        [],
    )


async def test_a_given_up_group_alert_and_group_note_are_recorded_too(
    sessions: SessionFactory,
) -> None:
    """T-65 (6): the group's e-mail and the group note of each of its offenses."""
    await case(sessions, 102)
    async with sessions.begin() as session:
        await create_offense_group(
            session,
            group_id=GROUP,
            rule_set_hash="0123456789ab",
            window_start=NOW,
            window_end=NOW + timedelta(hours=1),
        )
        await create_case(
            session,
            case_id=GROUP_CASE,
            source=CaseSource.GROUP,
            group_id=GROUP,
            sla_due_at=NOW,
            workflow_id=GROUP_CASE,
            run_id="run-1",
        )
    key = f"group_alert:{GROUP}:1"
    group_alert = email_call(
        case_id=GROUP_CASE,
        evaluation_no=1,
        email_kind="group_alert",
        level="high",
        group_id=GROUP,
        idempotency_key=key,
    )
    group_note = note_call(
        case_id=GROUP_CASE, offense_id=102, evaluation_no=1, run_marker="fedcba987654"
    )

    assert await activities(sessions).record_executor_failure(group_alert) is True
    assert await activities(sessions).record_executor_failure(group_note) is True

    async with sessions() as session:
        row = await get_notification(session, key)
        [note] = await list_notes(session, GROUP_CASE)
    assert row is not None
    assert (row.kind, row.group_id, row.level, row.status) == (
        EmailKind.GROUP_ALERT,
        GROUP,
        Level.HIGH,
        NotificationStatus.FAILED,
    )
    assert (note.offense_id, note.status, note.error) == (
        102,
        NoteStatus.FAILED,
        "executor_unavailable",
    )


async def test_an_email_the_executor_already_recorded_is_left_as_it_is(
    sessions: SessionFactory,
) -> None:
    await case(sessions)
    message = EmailMessage(
        kind=EmailKind.CASE_ALERT,
        recipients=["soc-1@example.com"],
        subject="[AI-SOC] YÜKSEK",
        template_id="case_alert",
        fields={},
        attachments=[],
        idempotency_key=f"case_alert:{CASE}:2",
    )
    async with sessions.begin() as session:
        await record_notification(
            session,
            message,
            status=NotificationStatus.FAILED,
            level=Level.CRITICAL,
            case_id=CASE,
            error="relay refused the e-mail",
        )

    assert await activities(sessions).record_executor_failure(email_call()) is False

    async with sessions() as session:
        row = await get_notification(session, f"case_alert:{CASE}:2")
    assert row is not None
    assert (row.error, row.recipients) == ("relay refused the e-mail", ["soc-1@example.com"])


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "note", "offense_id": None},
        {"kind": "note", "run_marker": None},
        {"kind": "email", "email_kind": None},
        {"kind": "email", "idempotency_key": None},
    ],
)
def test_a_call_without_what_the_record_needs_is_refused(changes: dict[str, object]) -> None:
    base = note_call() if changes["kind"] == "note" else email_call()
    with pytest.raises(ValueError, match="missing what the record needs"):
        AbandonedCall.model_validate(base.model_dump() | changes)


def test_a_group_alert_needs_its_group_and_a_case_alert_has_none() -> None:
    with pytest.raises(ValueError, match="missing what the record needs"):
        email_call(email_kind="group_alert")
    with pytest.raises(ValueError, match="missing what the record needs"):
        email_call(group_id=GROUP)


async def test_the_write_failure_alarm_counts_the_calls_the_case_gave_up(
    sessions: SessionFactory,
) -> None:
    """T-032 criterion 9: criterion 4's count sees the rows this activity writes, notes and
    e-mails alike, and its alarm opens for them."""
    from test_health_checks import health

    now = datetime.now(UTC)
    for number in range(1, 5):
        await case(sessions, 100 + number)
        recorder = ExecutorFailureActivities(sessions=sessions, clock=lambda: now)
        await recorder.record_executor_failure(
            note_call(case_id=f"case-{100 + number}", offense_id=100 + number)
        )
        await recorder.record_executor_failure(
            email_call(
                case_id=f"case-{100 + number}",
                idempotency_key=f"case_alert:case-{100 + number}:2",
            )
        )

    notices = await (await health(sessions, now=now)).check_write_failures()

    assert {(notice.subject, notice.counts["failures"]) for notice in notices} == {
        ("notes", 4),
        ("emails", 4),
    }
