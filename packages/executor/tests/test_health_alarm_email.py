"""The health alarm e-mail (T-032 criterion 8): a Turkish fixed template, the `health_alarm`
route, the idempotency key, and no kill switch: this one kind is sent with writes off, while
the case and group alerts are not."""

import pytest
from email_payloads import (
    ALARM_ID,
    INJECTED,
    FakeTransport,
    Sessions,
    add_recipients,
    allow_domains,
    case_alert,
    group_alert,
    health_alarm,
    notifications,
    route,
    switch_writes,
)

from ais0c_contracts import EmailKind
from ais0c_executor.common import is_clean
from ais0c_executor.email import (
    MAX_SUBJECT_LENGTH,
    EmailResult,
    EmailSender,
    HealthAlarm,
    InvalidEmail,
    alert_message,
    render_body,
)
from ais0c_storage import NotificationStatus

pytestmark = pytest.mark.anyio

TEAM = ("platform-1@example.com", "platform-2@example.com")
KEY = f"health_alarm:{ALARM_ID}:open:1"
SUBJECT = "[AI-SOC] Sağlık alarmı · AÇILDI · Log source sustu: 17"
BODY = """\
AI-SOC platform sağlık alarmı: AÇILDI

Alarm: Log source sustu
Konu: 17 · FW-DMZ-01

Açılış: 2026-10-02 14:05 · Bildirim: #1
Sessizlik (dk): 75
Eşik: 60

Platformun kendi sağlığı bozuldu; AI değerlendirmeleri etkilenmiş olabilir. İlgili bileşeni kontrol edin.

Bu e-postayı AI-SOC platformu otomatik olarak gönderdi; yanıtlamayın.
"""


@pytest.fixture
async def ready(sessions: Sessions) -> Sessions:
    """The team in an allowed domain, routed the way revision 0010 seeds it."""
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, *TEAM, list_name="analyst-eng")
    return sessions


def sender(sessions: Sessions, transport: FakeTransport) -> EmailSender:
    return EmailSender(sessions=sessions, transport=transport)


def test_the_template_is_turkish_and_fixed() -> None:
    message = alert_message(health_alarm(), TEAM)

    assert message.kind is EmailKind.HEALTH_ALARM
    assert message.subject == SUBJECT
    assert message.idempotency_key == KEY
    assert message.attachments == []
    assert render_body(message) == BODY


def test_a_resolved_alarm_says_the_cause_is_gone() -> None:
    message = alert_message(health_alarm(status="resolved", notification_no=2), TEAM)

    assert message.subject.startswith("[AI-SOC] Sağlık alarmı · DÜZELDİ · ")
    assert "Alarmın nedeni ortadan kalktı." in render_body(message)
    assert message.idempotency_key == f"health_alarm:{ALARM_ID}:resolved:2"


def test_each_notification_of_an_alarm_has_its_own_key() -> None:
    keys = {
        health_alarm(status="open", notification_no=1).idempotency_key,
        health_alarm(status="reminder", notification_no=2).idempotency_key,
        health_alarm(status="reminder", notification_no=3).idempotency_key,
        health_alarm(status="resolved", notification_no=4).idempotency_key,
    }
    assert len(keys) == 4


def test_text_from_qradar_cannot_add_a_line_or_a_header() -> None:
    alarm = health_alarm(subject_name=INJECTED, subject="17\r\nBcc: exfil@example.net")

    message = alert_message(alarm, TEAM)
    body = render_body(message)

    assert is_clean(message.subject)
    assert len(message.subject) <= MAX_SUBJECT_LENGTH
    assert "\nBcc:" not in body
    assert "\n[AI-SOC] KRİTİK" not in body
    # Konu is one line, however many the name had.
    assert len([line for line in body.splitlines() if line.startswith("Konu: ")]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"alarm_id": "not-a-uuid"},
        {"alarm_kind": "model_drift"},
        {"status": "closed"},
        {"notification_no": 0},
        {"subject": ""},
        {"counts": {"Bad Name": 1}},
        {"counts": {f"n{chr(97 + i)}": i for i in range(7)}},
    ],
)
def test_an_invalid_alarm_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        health_alarm(**changes)


def test_a_request_built_without_validation_is_checked_again() -> None:
    bad = HealthAlarm.model_construct(
        kind="health_alarm",
        alarm_id="x",
        alarm_kind="log_source_silent",
        subject="1",
        subject_name=None,
        status="open",
        notification_no=1,
        opened_at=health_alarm().opened_at,
        counts={},
    )
    with pytest.raises(InvalidEmail):
        alert_message(bad, TEAM)


async def test_it_goes_to_the_health_alarm_route_and_is_recorded_without_a_case(
    ready: Sessions,
) -> None:
    transport = FakeTransport()

    outcome = await sender(ready, transport).send_alert(health_alarm())

    assert (outcome.result, outcome.kind, outcome.idempotency_key) == (
        EmailResult.SENT,
        EmailKind.HEALTH_ALARM,
        KEY,
    )
    assert [message.recipients for message in transport.messages] == [list(TEAM)]
    (row,) = await notifications(ready)
    assert (row.kind, row.level, row.case_id, row.group_id, row.hunt_id) == (
        EmailKind.HEALTH_ALARM,
        None,
        None,
        None,
        None,
    )
    assert (row.status, row.error) == (NotificationStatus.SENT, None)


async def test_the_route_decides_who_gets_it(ready: Sessions) -> None:
    await add_recipients(ready, "oncall@example.com", list_name="oncall")
    await route(ready, "oncall", kind=EmailKind.HEALTH_ALARM, level=None)
    transport = FakeTransport()

    await sender(ready, transport).send_alert(health_alarm())

    assert [message.recipients for message in transport.messages] == [["oncall@example.com"]]


async def test_the_same_notification_is_sent_once(ready: Sessions) -> None:
    transport = FakeTransport()
    first = await sender(ready, transport).send_alert(health_alarm())
    second = await sender(ready, transport).send_alert(health_alarm())

    assert (first.result, second.result) == (EmailResult.SENT, EmailResult.ALREADY_SENT)
    assert len(transport.sent) == 1


async def test_it_is_sent_while_the_kill_switch_is_off(ready: Sessions) -> None:
    """Writes are off in shadow mode and after an AI incident; the alarm still reaches the team."""
    await switch_writes(ready, False, reason="AI incident, writes stopped.")
    transport = FakeTransport()

    outcome = await sender(ready, transport).send_alert(health_alarm())

    assert outcome.result is EmailResult.SENT
    assert len(transport.sent) == 1


async def test_a_case_and_a_group_alert_are_still_held_by_the_kill_switch(
    ready: Sessions,
) -> None:
    """The exemption is for the health alarm alone (negative test)."""
    await add_recipients(ready, "soc-1@example.com", list_name="operators")
    await add_recipients(ready, "soc-1@example.com", list_name="exec")
    await add_recipients(ready, "soc-1@example.com", list_name="analyst-eng")
    await switch_writes(ready, False, reason="AI incident, writes stopped.")
    transport = FakeTransport()
    email = sender(ready, transport)

    case = await email.send_alert(case_alert())
    group = await email.send_alert(group_alert())

    assert (case.result, group.result) == (EmailResult.WRITES_DISABLED, EmailResult.WRITES_DISABLED)
    assert transport.sent == []


async def test_an_alarm_with_no_recipients_is_recorded_as_failed(sessions: Sessions) -> None:
    await allow_domains(sessions, "example.com")
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(health_alarm())

    assert outcome.result is EmailResult.FAILED
    assert outcome.error is not None
    assert "health alarm" in outcome.error
    (row,) = await notifications(sessions)
    assert row.status is NotificationStatus.FAILED
    assert transport.sent == []


async def test_a_recipient_outside_the_allowed_domains_refuses_the_alarm(
    sessions: Sessions,
) -> None:
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, "x@example.net", list_name="analyst-eng")
    transport = FakeTransport()

    outcome = await sender(sessions, transport).send_alert(health_alarm())

    assert outcome.result is EmailResult.REJECTED
    assert transport.sent == []
