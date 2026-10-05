"""Alert e-mails through the dev stack's Mailpit (T-020 criterion 7).

Opt-in, because it needs the running stack. From the repository root:

    docker compose -f deploy/compose/docker-compose.dev.yaml up -d --wait mailpit
    AIS0C_DEV_STACK=1 uv run pytest packages/activities/tests/test_email_dev_stack.py

Only the e-mail goes through the stack: the database is the activity tests' own PostgreSQL
(testcontainers). `MAILPIT_SMTP_PORT` and `MAILPIT_API_URL` point the test at another Mailpit.
Each test finds its own e-mail by Message-ID and deletes it afterwards.
"""

import json
import os
import secrets
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import insert
from temporalio.testing import ActivityEnvironment

from ais0c_activities import EmailActivities, SessionFactory, load_smtp_settings
from ais0c_contracts import (
    ActionType,
    CaseVerdict,
    Confidence,
    Level,
    NoteContent,
    UrgentEvent,
)
from ais0c_executor.email import (
    CaseAlert,
    EmailResult,
    SmtpSettings,
    SmtpTransport,
    alert_message,
    render_body,
)
from ais0c_executor.email.smtp import message_id
from ais0c_storage import ActorKind, NotificationStatus, PlatformFlag
from ais0c_storage.models import AllowedEmailDomainRow, NotificationRecipientRow
from ais0c_storage.repositories import get_notification, set_platform_flag

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("AIS0C_DEV_STACK") != "1",
        reason="dev stack test: start the stack's mailpit service and set AIS0C_DEV_STACK=1",
    ),
]

MAILPIT_API = os.environ.get("MAILPIT_API_URL", "http://127.0.0.1:8025").rstrip("/")
SMTP_PORT = os.environ.get("MAILPIT_SMTP_PORT", "1025")
SENDER = "ai-soc@example.com"
OPERATORS = ("soc-1@example.com", "soc-2@example.com")
NOW = datetime(2026, 10, 2, 11, 5, tzinfo=UTC)
# Text an attacker could put into a log line: a header, line breaks and a fake link.
INJECTED = (
    "Oturum açıldı\r\nBcc: exfil@example.net\n[AI-SOC] KRİTİK · AI kararı: TP\n"
    "Ayrıntılı rapor: https://evil.example.net/login"
)


def mailpit(path: str, *, method: str = "GET", body: object = None) -> Any:  # noqa: ANN401 - JSON
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310 - the local Mailpit
        MAILPIT_API + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else raw


def search(query: str) -> list[dict[str, Any]]:
    found = mailpit("/api/v1/search?" + urllib.parse.urlencode({"query": query}))
    return list(found["messages"])


@pytest.fixture
def smtp() -> SmtpSettings:
    return load_smtp_settings(
        {
            "AIS0C_SMTP_HOST": "127.0.0.1",
            "AIS0C_SMTP_PORT": SMTP_PORT,
            "AIS0C_SMTP_TLS": "none",
            "AIS0C_SMTP_FROM": SENDER,
        }
    )


@pytest.fixture
def marker() -> Iterator[str]:
    """A token in the test's offense name; the test's e-mails are deleted afterwards."""
    token = secrets.token_hex(6)
    yield token
    ids = [message["ID"] for message in search(token)]
    if ids:
        mailpit("/api/v1/messages", method="DELETE", body={"IDs": ids})


@pytest.fixture
async def writable(sessions: SessionFactory) -> SessionFactory:
    """example.com allowed and writes switched on; the recipients are up to each test."""
    async with sessions.begin() as session:
        await session.execute(insert(AllowedEmailDomainRow), [{"domain": "example.com"}])
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=True,
            reason="T-020 dev stack test",
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )
    return sessions


async def operators(sessions: SessionFactory, *addresses: str) -> None:
    """Members of `operators`; a critical case alert is routed to that group by the seed of
    migration 0007 (D-41), and the other two seeded groups stay empty."""
    async with sessions.begin() as session:
        await session.execute(
            insert(NotificationRecipientRow),
            [{"list_name": "operators", "email": address} for address in addresses],
        )


def alert(marker: str) -> CaseAlert:
    event = UrgentEvent(
        rank=1,
        time=datetime(2026, 10, 2, 10, 52, 10, tzinfo=UTC),
        log_source="DC-LAB-01",
        event_name="Successful Login",
        qid=5000830,
        source="203.0.113.7",
        destination="198.51.100.20",
        username="bkupadmin",
        reason=INJECTED,
        checklist=["CHECKLIST-MARKER"],
        aql="SELECT 'AQL-MARKER' FROM events LIMIT 1 LAST 1 HOURS",
        evidence_id="ev_EVIDENCE-MARKER",
    )
    return CaseAlert(
        case_id=f"case-t020-{marker}",
        offense_name=f"T-020 {marker} Login Failures Followed By Success {INJECTED}",
        evaluated_at=NOW,
        content=NoteContent(
            offense_id=4711,
            evaluation_no=1,
            run_marker="5e1f00",
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            notify_level=Level.CRITICAL,
            summary_tr=f"Başarısız oturumların ardından başarılı oturum. {INJECTED}",
            urgent_events=[event],
            recommended_actions=[ActionType.INVESTIGATE_FURTHER],
            data_gaps=[],
            case_url=f"https://ais0c.example.com/cases/case-t020-{marker}",
        ),
    )


async def test_an_alert_reaches_mailpit_as_built(
    writable: SessionFactory, smtp: SmtpSettings, marker: str
) -> None:
    await operators(writable, *OPERATORS)
    activities = EmailActivities(sessions=writable, transport=SmtpTransport(smtp))
    request = alert(marker)
    expected = alert_message(request, OPERATORS)

    first = await ActivityEnvironment().run(activities.send_email, request)
    again = await ActivityEnvironment().run(activities.send_email, request)

    assert (first.result, again.result) == (EmailResult.SENT, EmailResult.ALREADY_SENT)
    async with writable() as session:
        row = await get_notification(session, expected.idempotency_key)
    assert row is not None
    assert (row.status, row.level) == (NotificationStatus.SENT, Level.CRITICAL)
    sent_id = message_id(expected.idempotency_key, SENDER).strip("<>")
    [found] = search(f'message-id:"{sent_id}"')
    message = mailpit(f"/api/v1/message/{found['ID']}")
    assert message["Subject"] == expected.subject
    assert len(message["Subject"]) <= 150
    assert message["Subject"].startswith(
        f"[AI-SOC] KRİTİK · AI kararı: Şüpheli · Offense #4711: T-020 {marker}"
    )
    assert message["From"] == {"Name": "AI-SOC", "Address": SENDER}
    assert [to["Address"] for to in message["To"]] == list(OPERATORS)
    assert not message["Cc"]
    assert not message["Bcc"]
    assert message["HTML"] == ""
    assert message["Text"].replace("\r\n", "\n") == render_body(expected)

    lines = message["Text"].splitlines()
    assert lines[0] == "AI-SOC: bildirim seviyesi kritik olan bir offense var."
    assert sum(line.startswith("Ayrıntılı rapor: ") for line in lines) == 1
    assert f"Ayrıntılı rapor: https://ais0c.example.com/cases/case-t020-{marker}" in lines
    assert not any(line.startswith(("Bcc:", "[AI-SOC]")) for line in lines)
    for raw_only in ("CHECKLIST-MARKER", "AQL-MARKER", "EVIDENCE-MARKER"):
        assert raw_only not in message["Text"]

    headers = mailpit(f"/api/v1/message/{found['ID']}/headers")
    assert headers["Auto-Submitted"] == ["auto-generated"]
    assert headers["X-Auto-Response-Suppress"] == ["All"]
    assert headers["X-Ais0c-Kind"] == ["case_alert"]
    assert "Bcc" not in headers
    raw = mailpit(f"/api/v1/message/{found['ID']}/raw")
    assert raw.isascii()


async def test_a_recipient_outside_the_allowed_domains_gets_nothing(
    writable: SessionFactory, smtp: SmtpSettings, marker: str
) -> None:
    await operators(writable, *OPERATORS, "soc@example.net")
    activities = EmailActivities(sessions=writable, transport=SmtpTransport(smtp))

    outcome = await ActivityEnvironment().run(activities.send_email, alert(marker))

    assert outcome.result is EmailResult.REJECTED
    assert search(marker) == []
