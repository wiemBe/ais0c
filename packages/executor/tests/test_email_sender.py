"""Sending an alert once (T-020 criteria 3-6): the allowed domain check, no second e-mail for one
idempotency key, the kill switch right before sending, and the level rule on re-evaluation."""

import asyncio
from datetime import UTC, datetime

import pytest
from email_payloads import (
    GROUP_ID,
    OPERATORS,
    FakeTransport,
    Sessions,
    add_recipients,
    allow_domains,
    case_alert,
    email_audit,
    evaluation,
    group_alert,
    note_content,
    notification,
    notifications,
    operators_in_example_com,
    switch_writes,
)
from sqlalchemy import delete, text

from ais0c_contracts import EmailKind, Level
from ais0c_executor.email import (
    CaseAlert,
    EmailOutcome,
    EmailResult,
    EmailSender,
    EmailTransportError,
    InvalidEmail,
    alert_message,
    render_body,
)
from ais0c_storage import ActorKind, NotificationStatus
from ais0c_storage.models import NotificationRecipientRow
from ais0c_storage.repositories import append_audit, record_notification

pytestmark = pytest.mark.anyio

SENT_AT = datetime(2026, 10, 2, 11, 6, tzinfo=UTC)
SENT, ALREADY_SENT, NOT_NEEDED = EmailResult.SENT, EmailResult.ALREADY_SENT, EmailResult.NOT_NEEDED
REJECTED, WRITES_DISABLED, FAILED = (
    EmailResult.REJECTED,
    EmailResult.WRITES_DISABLED,
    EmailResult.FAILED,
)
KEY = "case_alert:case-12345:1"


@pytest.fixture
async def ready(sessions: Sessions) -> Sessions:
    """Two operators in an allowed domain, and writes switched on."""
    await operators_in_example_com(sessions)
    await switch_writes(sessions, True)
    return sessions


def sender(sessions: Sessions, transport: FakeTransport) -> EmailSender:
    return EmailSender(sessions=sessions, transport=transport, clock=lambda: SENT_AT)


async def test_a_case_alert_is_sent_and_recorded(ready: Sessions) -> None:
    transport = FakeTransport()

    outcome = await sender(ready, transport).send_alert(case_alert())

    assert outcome == EmailOutcome(result=SENT, kind=EmailKind.CASE_ALERT, idempotency_key=KEY)
    expected = alert_message(case_alert(), OPERATORS)
    assert transport.sent == [(expected, render_body(expected))]
    row = await notification(ready, KEY)
    assert row is not None
    assert (row.kind, row.case_id, row.group_id, row.hunt_id) == (
        EmailKind.CASE_ALERT,
        "case-12345",
        None,
        None,
    )
    assert (row.status, row.sent_at, row.level) == (NotificationStatus.SENT, SENT_AT, Level.HIGH)
    assert (row.recipients, row.subject) == (list(OPERATORS), expected.subject)
    [entry] = await email_audit(ready)
    assert (entry.actor_kind, entry.actor_id, entry.action) == (
        ActorKind.SYSTEM,
        "action-executor",
        "email.send",
    )
    assert (entry.object_type, entry.object_id) == ("case", "case-12345")
    assert entry.details == {
        "kind": "case_alert",
        "idempotency_key": KEY,
        "case_id": "case-12345",
        "evaluation_no": 1,
        "level": "high",
        "subject": expected.subject,
        "recipients": list(OPERATORS),
        "message_id": "<fake.1@example.com>",
        "refused_by_relay": [],
    }


# --- criterion 4: one e-mail per idempotency key --------------------------------------------


async def test_a_retried_activity_sends_one_email(ready: Sessions) -> None:
    """The retry after a send that was recorded: a new attempt, possibly on another worker."""
    transport = FakeTransport()

    first = await sender(ready, transport).send_alert(case_alert())
    second = await sender(ready, transport).send_alert(case_alert())

    assert (first.result, second.result) == (SENT, ALREADY_SENT)
    assert second.idempotency_key == KEY
    assert len(transport.sent) == 1
    assert transport.connections == 1
    assert len(await notifications(ready)) == 1
    assert len(await email_audit(ready)) == 1


async def test_two_attempts_at_the_same_time_send_one_email(ready: Sessions) -> None:
    """A retry that starts while a slow first attempt still runs waits for it."""
    transport = FakeTransport(delay=0.3)

    outcomes = await asyncio.gather(
        sender(ready, transport).send_alert(case_alert()),
        sender(ready, transport).send_alert(case_alert()),
    )

    assert sorted(outcome.result for outcome in outcomes) == [ALREADY_SENT, SENT]
    assert len(transport.sent) == 1


async def test_a_failed_send_that_may_pass_is_recorded_retried_and_sent_once(
    ready: Sessions,
) -> None:
    failure = EmailTransportError("send: the relay answered 451 4.3.0 later", retryable=True)
    transport = FakeTransport(failures=[failure])

    with pytest.raises(EmailTransportError, match="451") as raised:
        await sender(ready, transport).send_alert(case_alert())
    assert raised.value is failure
    row = await notification(ready, KEY)
    assert row is not None
    assert (row.status, row.sent_at) == (NotificationStatus.FAILED, None)

    retry = await sender(ready, transport).send_alert(case_alert())

    assert retry.result is SENT
    assert len(transport.sent) == 1
    rows = await notifications(ready)
    assert [(row.status, row.sent_at) for row in rows] == [(NotificationStatus.SENT, SENT_AT)]


async def test_an_unreachable_relay_is_recorded_and_retried(ready: Sessions) -> None:
    unreachable = EmailTransportError("connect: ConnectionRefusedError", retryable=True)
    transport = FakeTransport(connect_failure=unreachable)

    with pytest.raises(EmailTransportError, match="connect"):
        await sender(ready, transport).send_alert(case_alert())

    row = await notification(ready, KEY)
    assert row is not None
    assert row.status is NotificationStatus.FAILED


async def test_an_email_the_relay_refused_for_good_is_returned_as_failed(ready: Sessions) -> None:
    refused = EmailTransportError("send: the relay answered 554 5.7.1 refused", retryable=False)
    transport = FakeTransport(failures=[refused])

    outcome = await sender(ready, transport).send_alert(case_alert())

    assert outcome.result is FAILED
    assert outcome.error == "send: the relay answered 554 5.7.1 refused"
    row = await notification(ready, KEY)
    assert row is not None
    assert row.status is NotificationStatus.FAILED
    assert await email_audit(ready) == []


# --- criterion 3: allowed domains -----------------------------------------------------------


async def test_one_recipient_outside_the_allowed_domains_stops_the_whole_email(
    sessions: Sessions,
) -> None:
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, "soc-1@example.com", "soc@example.net", "soc-2@example.com")
    await switch_writes(sessions, True)
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(case_alert())

    assert outcome.result is REJECTED
    assert outcome.error == (
        "1 of 3 recipients are not plain addresses in the allowed domains; the e-mail went to "
        "nobody"
    )
    assert transport.connections == 0
    assert transport.sent == []
    row = await notification(sessions, KEY)
    assert row is not None
    assert (row.status, row.sent_at, row.level) == (NotificationStatus.REJECTED, None, Level.HIGH)
    assert row.recipients == ["soc-1@example.com", "soc-2@example.com", "soc@example.net"]
    [entry] = await email_audit(sessions)
    assert (entry.action, entry.object_type, entry.object_id) == (
        "email.reject",
        "case",
        "case-12345",
    )
    assert entry.details["refused"] == [
        {"address": "soc@example.net", "reason": "domain_not_allowed"}
    ]
    assert entry.details["level"] == "high"


async def test_an_address_that_could_add_a_header_is_rejected(sessions: Sessions) -> None:
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, "soc-1@example.com", "soc-2@example.com\r\nBcc: x@example.net")
    await switch_writes(sessions, True)
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(case_alert())

    assert outcome.result is REJECTED
    assert transport.connections == 0
    [entry] = await email_audit(sessions)
    assert entry.details["refused"] == [
        {"address": "soc-2@example.com\r\nBcc: x@example.net", "reason": "not_an_address"}
    ]


async def test_a_rejected_alert_goes_out_once_the_list_is_fixed(sessions: Sessions) -> None:
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, *OPERATORS, "soc@example.net")
    await switch_writes(sessions, True)
    transport = FakeTransport()
    assert (await sender(sessions, transport).send_alert(case_alert())).result is REJECTED

    async with sessions.begin() as session:
        await session.execute(
            delete(NotificationRecipientRow).where(
                NotificationRecipientRow.email == "soc@example.net"
            )
        )
    retry = await sender(sessions, transport).send_alert(case_alert())

    assert retry.result is SENT
    assert [message.recipients for message in transport.messages] == [list(OPERATORS)]
    [row] = await notifications(sessions)
    assert (row.status, row.recipients) == (NotificationStatus.SENT, list(OPERATORS))
    assert [entry.action for entry in await email_audit(sessions)] == [
        "email.reject",
        "email.send",
    ]


async def test_an_empty_recipient_list_is_a_failure(sessions: Sessions) -> None:
    await allow_domains(sessions, "example.com")
    await switch_writes(sessions, True)
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(case_alert())

    assert outcome.result is FAILED
    assert outcome.error == "the operators recipient list is empty"
    assert transport.connections == 0
    row = await notification(sessions, KEY)
    assert row is not None
    assert (row.status, row.recipients) == (NotificationStatus.FAILED, [])


# --- criterion 5: the kill switch -----------------------------------------------------------


async def test_in_shadow_mode_nothing_is_sent_or_recorded(sessions: Sessions) -> None:
    await operators_in_example_com(sessions)
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(case_alert())

    assert outcome.result is WRITES_DISABLED
    assert outcome.error is not None
    assert "never switched on" in outcome.error
    assert transport.connections == 0
    assert await notifications(sessions) == []
    assert await email_audit(sessions) == []


async def test_a_switch_off_after_the_email_is_ready_stops_it(ready: Sessions) -> None:
    """The flag goes off once the e-mail is built and the relay connected: nothing is sent."""

    async def switch_off() -> None:
        await switch_writes(ready, False, reason="Yanlış alarm fırtınası, e-postalar durduruldu.")

    transport = FakeTransport(on_connect=switch_off)

    outcome = await sender(ready, transport).send_alert(case_alert())

    assert outcome.result is WRITES_DISABLED
    assert outcome.error is not None
    assert "switched off by admin01" in outcome.error
    assert transport.connections == 1
    assert transport.attempts == 0
    assert await notifications(ready) == []

    await switch_writes(ready, True)
    transport.on_connect = None
    assert (await sender(ready, transport).send_alert(case_alert())).result is SENT
    assert len(transport.sent) == 1


async def test_the_domain_check_does_not_wait_for_writes(sessions: Sessions) -> None:
    """A bad recipient list shows up in shadow mode already, before the canary starts."""
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, "soc@example.net")
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(case_alert())

    assert outcome.result is REJECTED
    assert transport.connections == 0


# --- criterion 6: re-evaluations ------------------------------------------------------------


async def test_a_re_evaluation_is_emailed_only_when_its_level_goes_up(ready: Sessions) -> None:
    transport = FakeTransport()
    steps = [(1, "high"), (2, "high"), (3, "critical"), (4, "critical"), (5, "high"), (6, "low")]

    results = [
        (await sender(ready, transport).send_alert(evaluation(number, level))).result
        for number, level in steps
    ]

    assert results == [SENT, NOT_NEEDED, SENT, NOT_NEEDED, NOT_NEEDED, NOT_NEEDED]
    assert [message.subject[:24] for message in transport.messages] == [
        "[AI-SOC] YÜKSEK · AI kar",
        "[AI-SOC] KRİTİK · AI kar",
    ]
    assert [(row.idempotency_key, row.level) for row in await notifications(ready)] == [
        ("case_alert:case-12345:1", Level.HIGH),
        ("case_alert:case-12345:3", Level.CRITICAL),
    ]


async def test_the_earlier_levels_are_the_ones_notifications_records(ready: Sessions) -> None:
    """Only a `sent` case alert of the case counts, by its `level`. A rejected alert does not,
    and neither does an audit entry without its `notifications` row."""
    async with ready.begin() as session:
        await record_notification(
            session,
            alert_message(evaluation(1, "critical"), OPERATORS),
            status=NotificationStatus.REJECTED,
            level=Level.CRITICAL,
            case_id="case-12345",
        )
        await append_audit(
            session,
            actor_kind=ActorKind.SYSTEM,
            actor_id="action-executor",
            action="email.send",
            object_type="case",
            object_id="case-12345",
            details={"kind": "case_alert", "level": "critical"},
        )
    transport = FakeTransport()

    second = await sender(ready, transport).send_alert(evaluation(2, "high"))

    async with ready.begin() as session:
        await record_notification(
            session,
            alert_message(evaluation(3, "critical"), OPERATORS),
            status=NotificationStatus.SENT,
            level=Level.CRITICAL,
            case_id="case-12345",
            sent_at=SENT_AT,
        )
    fourth = await sender(ready, transport).send_alert(evaluation(4, "critical"))

    assert (second.result, fourth.result) == (SENT, NOT_NEEDED)
    assert len(transport.sent) == 1


async def test_a_sent_alert_recorded_before_the_level_column_does_not_block(
    ready: Sessions,
) -> None:
    """A row from before migration 0003 has no level; skipping it can send one e-mail too many,
    never one too few."""
    async with ready.begin() as session:
        await session.execute(
            text(
                "INSERT INTO notifications (id, kind, case_id, recipients, subject,"
                " idempotency_key, status, sent_at) VALUES (gen_random_uuid(), 'case_alert',"
                " 'case-12345', ARRAY['soc-1@example.com'], 'old', 'case_alert:case-12345:1',"
                " 'sent', now())"
            )
        )

    outcome = await sender(ready, FakeTransport()).send_alert(evaluation(2, "high"))

    assert outcome.result is SENT


async def test_an_evaluation_below_high_is_not_emailed(ready: Sessions) -> None:
    transport = FakeTransport()

    for level in ("low", "medium"):
        outcome = await sender(ready, transport).send_alert(evaluation(1, level))
        assert outcome.result is NOT_NEEDED
        assert outcome.error is None

    assert transport.connections == 0
    assert await notifications(ready) == []


async def test_an_alert_that_was_not_sent_does_not_count(sessions: Sessions) -> None:
    await operators_in_example_com(sessions)
    transport = FakeTransport()
    first = await sender(sessions, transport).send_alert(evaluation(1, "critical"))
    await switch_writes(sessions, True)

    second = await sender(sessions, transport).send_alert(evaluation(2, "high"))

    assert (first.result, second.result) == (WRITES_DISABLED, SENT)


async def test_the_levels_of_another_case_do_not_count(ready: Sessions) -> None:
    transport = FakeTransport()
    await sender(ready, transport).send_alert(evaluation(1, "critical"))

    other = case_alert(case_id="case-777", content=note_content(offense_id=777))
    outcome = await sender(ready, transport).send_alert(other)

    assert outcome.result is SENT
    assert outcome.idempotency_key == "case_alert:case-777:1"


# --- group alerts ---------------------------------------------------------------------------


async def test_a_group_is_emailed_once(ready: Sessions) -> None:
    transport = FakeTransport()

    first = await sender(ready, transport).send_alert(group_alert())
    again = await sender(ready, transport).send_alert(
        group_alert(evaluation_no=2, notify_level="critical")
    )

    assert (first.result, again.result) == (SENT, ALREADY_SENT)
    assert first.kind is EmailKind.GROUP_ALERT
    assert len(transport.sent) == 1
    [row] = await notifications(ready)
    assert (row.kind, row.case_id, row.group_id, row.level) == (
        EmailKind.GROUP_ALERT,
        f"group-{GROUP_ID}",
        GROUP_ID,
        Level.HIGH,
    )
    [entry] = await email_audit(ready)
    assert (entry.object_type, entry.object_id) == ("offense_group", GROUP_ID)
    assert entry.details["group_id"] == GROUP_ID


async def test_a_group_below_high_is_not_emailed(ready: Sessions) -> None:
    transport = FakeTransport()

    outcome = await sender(ready, transport).send_alert(group_alert(notify_level="medium"))

    assert outcome.result is NOT_NEEDED
    assert transport.connections == 0


# --- invalid requests -----------------------------------------------------------------------


async def test_an_invalid_request_sends_and_records_nothing(ready: Sessions) -> None:
    valid = case_alert()
    unchecked = CaseAlert.model_construct(
        case_id=valid.case_id,
        offense_name=valid.offense_name,
        evaluated_at=valid.evaluated_at,
        content=note_content().model_copy(update={"case_url": "javascript:alert(1)"}),
    )
    transport = FakeTransport()

    with pytest.raises(InvalidEmail, match="case_url"):
        await sender(ready, transport).send_alert(unchecked)

    assert transport.connections == 0
    assert await notifications(ready) == []
