"""The SMTP relay (T-020 criterion 1): host, port, TLS and sender come from the settings; the
e-mail goes out encrypted unless TLS is turned off, and relay errors say whether a retry may
succeed."""

import smtplib
import socket
import ssl
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from email_payloads import OPERATORS, case_alert
from pydantic import ValidationError
from smtp_server import Certificates, SmtpServer, make_certificates

from ais0c_contracts import EmailMessage
from ais0c_executor.email import (
    DEFAULT_PORTS,
    EmailTransportError,
    InvalidEmail,
    SendReceipt,
    SmtpSettings,
    SmtpTransport,
    TlsMode,
    alert_message,
    mime_message,
    render_body,
)
from ais0c_executor.email.smtp import message_id, transport_error

pytestmark = pytest.mark.anyio

SENDER = "ai-soc@example.com"
PASSWORD = "s3cret-pass"  # noqa: S105 - a test value
SENT_AT = datetime(2026, 10, 2, 11, 6, tzinfo=UTC)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Certificates:
    return make_certificates(tmp_path_factory.mktemp("certificates"))


@pytest.fixture
def message() -> EmailMessage:
    return alert_message(case_alert(), OPERATORS)


def settings(port: int, tls: TlsMode = TlsMode.NONE, **changes: object) -> SmtpSettings:
    values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": port,
        "tls": tls,
        "sender": SENDER,
        "timeout": 5,
    }
    return SmtpSettings.model_validate(values | changes)


async def send(smtp: SmtpSettings, message: EmailMessage) -> SendReceipt:
    transport = SmtpTransport(smtp, clock=lambda: SENT_AT)
    async with transport.connect() as connection:
        return await connection.send(message, render_body(message))


@pytest.fixture
def closed_port() -> Iterator[int]:
    """A port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        yield probe.getsockname()[1]


# --- settings -------------------------------------------------------------------------------


def test_the_port_follows_the_tls_mode_unless_it_is_given() -> None:
    assert SmtpSettings(host="relay.example.com", sender=SENDER).tls is TlsMode.STARTTLS
    for mode, port in DEFAULT_PORTS.items():
        assert (
            SmtpSettings(host="relay.example.com", sender=SENDER, tls=mode).effective_port == port
        )
    assert settings(2525).effective_port == 2525
    assert (DEFAULT_PORTS[TlsMode.STARTTLS], DEFAULT_PORTS[TlsMode.IMPLICIT]) == (587, 465)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"username": "ais0c", "password": PASSWORD}, "login needs TLS"),
        ({"tls": TlsMode.STARTTLS, "username": "ais0c"}, "go together"),
        ({"tls": TlsMode.STARTTLS, "password": PASSWORD}, "go together"),
        ({"tls": TlsMode.STARTTLS, "username": "ais 0c", "password": "x"}, "printable"),
        ({"host": "relay.example.com\r\nMAIL FROM:<x@example.net>"}, "host"),
        ({"host": "relay example.com"}, "host"),
        ({"host": ""}, "host"),
        ({"sender": "AI-SOC <ai-soc@example.com>"}, "sender"),
        ({"sender": "ai-soc@example.com\r\nBcc: x@example.net"}, "sender"),
        ({"port": 0}, "port"),
        ({"port": 65536}, "port"),
        ({"timeout": 0}, "timeout"),
        ({"tls": "ssl"}, "tls"),
        ({"ca_file": "/nonexistent/ca.pem", "tls": TlsMode.STARTTLS}, "ca_file"),
        ({"smtp_password": "x"}, "smtp_password"),
    ],
    ids=[
        "login-without-tls",
        "username-alone",
        "password-alone",
        "username-with-a-space",
        "host-with-a-command",
        "host-with-a-space",
        "empty-host",
        "sender-with-a-name",
        "sender-with-a-header",
        "port-0",
        "port-65536",
        "timeout-0",
        "unknown-tls-mode",
        "missing-ca-file",
        "unknown-setting",
    ],
)
def test_unsafe_or_invalid_settings_are_refused(changes: dict[str, object], problem: str) -> None:
    values = settings(25).model_dump() | changes
    with pytest.raises(ValidationError, match=problem):
        SmtpSettings.model_validate(values)


def test_a_ca_file_needs_tls(certificates: Certificates) -> None:
    with pytest.raises(ValidationError, match="ca_file needs TLS"):
        settings(25, ca_file=certificates.ca)


def test_the_password_is_not_shown() -> None:
    smtp = settings(587, TlsMode.STARTTLS, username="ais0c", password=PASSWORD)

    assert PASSWORD not in repr(smtp)
    assert PASSWORD not in str(smtp.model_dump())


# --- the e-mail on the wire -----------------------------------------------------------------


async def test_an_email_reaches_the_relay_as_built(message: EmailMessage) -> None:
    async with SmtpServer() as server:
        receipt = await send(settings(server.port), message)

    [received] = server.received
    assert received.sender == SENDER
    assert received.recipients == list(OPERATORS)
    assert receipt == SendReceipt(message_id=message_id(message.idempotency_key, SENDER))
    # 7-bit on the wire: the relay needs neither 8BITMIME nor SMTPUTF8.
    assert received.data.isascii()
    mail = received.message
    assert mail["From"] == f"AI-SOC <{SENDER}>"
    assert mail["To"] == ", ".join(OPERATORS)
    assert mail["Subject"] == message.subject
    assert mail["Message-ID"] == receipt.message_id
    assert mail["Date"].datetime == SENT_AT
    assert mail["Auto-Submitted"] == "auto-generated"
    assert mail["X-Auto-Response-Suppress"] == "All"
    assert mail["X-AIS0C-Kind"] == "case_alert"
    assert mail.get_content_type() == "text/plain"
    assert mail.get_content_charset() == "utf-8"
    assert mail["Content-Transfer-Encoding"] == "quoted-printable"
    # SMTP lines end in CRLF.
    assert mail.get_content() == render_body(message).replace("\n", "\r\n")
    assert mail["Bcc"] is None
    assert mail["Cc"] is None


async def test_every_attempt_carries_the_same_message_id(message: EmailMessage) -> None:
    other = message.model_copy(update={"idempotency_key": "case_alert:case-12345:2"})

    async with SmtpServer() as server:
        await send(settings(server.port), message)
        await send(settings(server.port), message)
        await send(settings(server.port), other)

    ids = [received.message["Message-ID"] for received in server.received]
    assert ids[0] == ids[1] != ids[2]
    assert ids[0].startswith("<ais0c.")
    assert ids[0].endswith("@example.com>")


def test_a_header_cannot_be_built_from_a_value_that_is_not_checked(
    message: EmailMessage,
) -> None:
    body = render_body(message)
    for changes in (
        {"recipients": ["soc-1@example.com\r\nBcc: x@example.net"]},
        {"recipients": ["soc-1@example.com, x@example.net"]},
        {"recipients": []},
        {"subject": "[AI-SOC] YÜKSEK\r\nBcc: x@example.net"},
    ):
        with pytest.raises(InvalidEmail):
            mime_message(message.model_copy(update=changes), body, sender=SENDER, sent_at=SENT_AT)
    with pytest.raises(InvalidEmail):
        mime_message(
            message, body, sender="ai-soc@example.com\nBcc: x@example.net", sent_at=SENT_AT
        )


# --- relay replies --------------------------------------------------------------------------


async def test_a_relay_that_refuses_one_recipient_takes_the_rest(message: EmailMessage) -> None:
    async with SmtpServer(replies={"soc-2@example.com": "550 5.1.1 No such user"}) as server:
        receipt = await send(settings(server.port), message)

    assert receipt.refused == ("soc-2@example.com",)
    assert [received.recipients for received in server.received] == [["soc-1@example.com"]]


@pytest.mark.parametrize(
    ("replies", "retryable"),
    [
        ({"MAIL": "451 4.3.0 Try again later"}, True),
        ({"MAIL": "550 5.7.1 Sender not allowed"}, False),
        ({"DATA": "452 4.3.1 Insufficient storage"}, True),
        ({"DATA": "554 5.7.1 Message refused"}, False),
        ({OPERATORS[0]: "450 4.2.1 Busy", OPERATORS[1]: "451 4.3.0 Later"}, True),
        ({OPERATORS[0]: "550 5.1.1 No such user", OPERATORS[1]: "451 4.3.0 Later"}, False),
    ],
    ids=["mail-4xx", "mail-5xx", "data-4xx", "data-5xx", "rcpt-4xx", "rcpt-mixed"],
)
async def test_a_refused_email_says_whether_a_retry_may_succeed(
    message: EmailMessage, replies: dict[str, str], retryable: bool
) -> None:
    async with SmtpServer(replies=replies) as server:
        with pytest.raises(EmailTransportError) as raised:
            await send(settings(server.port), message)

    assert raised.value.retryable is retryable
    assert server.received == []


async def test_an_unreachable_relay_may_be_retried(message: EmailMessage, closed_port: int) -> None:
    with pytest.raises(EmailTransportError, match="connect") as raised:
        await send(settings(closed_port), message)

    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (smtplib.SMTPServerDisconnected("Connection unexpectedly closed"), True),
        (smtplib.SMTPConnectError(421, b"Too busy"), True),
        (smtplib.SMTPConnectError(554, b"No service"), False),
        (smtplib.SMTPAuthenticationError(535, b"Bad credentials"), False),
        (smtplib.SMTPAuthenticationError(454, b"Temporary failure"), True),
        (smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."), False),
        (smtplib.SMTPRecipientsRefused({}), False),
        (ConnectionRefusedError(111, "Connection refused"), True),
        (TimeoutError("timed out"), True),
        (ssl.SSLCertVerificationError(1, "certificate verify failed"), False),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_errors_of_each_kind_are_classified(error: Exception, retryable: bool) -> None:
    assert transport_error("send", error).retryable is retryable


# --- TLS and login --------------------------------------------------------------------------


async def test_starttls_against_the_company_ca(
    message: EmailMessage, certificates: Certificates
) -> None:
    async with SmtpServer(certificates=certificates, starttls=True) as server:
        await send(settings(server.port, TlsMode.STARTTLS, ca_file=certificates.ca), message)

    [received] = server.received
    assert received.tls is True
    assert server.commands[:3] == ["EHLO", "STARTTLS", "EHLO"]


async def test_a_certificate_that_does_not_verify_is_refused(
    message: EmailMessage, certificates: Certificates
) -> None:
    """Without the company's CA the test certificate is unknown: nothing is sent."""
    async with SmtpServer(certificates=certificates, starttls=True) as server:
        with pytest.raises(EmailTransportError, match="certificate") as raised:
            await send(settings(server.port, TlsMode.STARTTLS), message)

    assert raised.value.retryable is False
    assert server.received == []
    assert server.mail_commands == 0


async def test_a_relay_without_starttls_gets_nothing_in_clear_text(
    message: EmailMessage, certificates: Certificates
) -> None:
    async with SmtpServer() as server:
        with pytest.raises(EmailTransportError, match="STARTTLS") as raised:
            await send(settings(server.port, TlsMode.STARTTLS, ca_file=certificates.ca), message)

    assert raised.value.retryable is False
    assert server.mail_commands == 0
    assert server.received == []


async def test_implicit_tls(message: EmailMessage, certificates: Certificates) -> None:
    async with SmtpServer(certificates=certificates, implicit_tls=True) as server:
        await send(settings(server.port, TlsMode.IMPLICIT, ca_file=certificates.ca), message)

    assert [received.tls for received in server.received] == [True]


async def test_the_login_happens_inside_tls(
    message: EmailMessage, certificates: Certificates
) -> None:
    login = {"username": "ais0c-relay", "password": PASSWORD, "ca_file": certificates.ca}
    async with SmtpServer(
        certificates=certificates, starttls=True, login=("ais0c-relay", PASSWORD)
    ) as server:
        await send(settings(server.port, TlsMode.STARTTLS, **login), message)

    [received] = server.received
    assert (received.tls, received.user) == (True, "ais0c-relay")
    assert server.commands.index("AUTH") > server.commands.index("STARTTLS")


async def test_a_refused_login_is_not_retried(
    message: EmailMessage, certificates: Certificates
) -> None:
    login = {"username": "ais0c-relay", "password": "wrong-pass", "ca_file": certificates.ca}
    async with SmtpServer(
        certificates=certificates, starttls=True, login=("ais0c-relay", PASSWORD)
    ) as server:
        with pytest.raises(EmailTransportError, match="535") as raised:
            await send(settings(server.port, TlsMode.STARTTLS, **login), message)

    assert raised.value.retryable is False
    assert server.received == []


def test_the_test_certificates_are_files(certificates: Certificates) -> None:
    assert all(Path(path).is_file() for path in (certificates.ca, certificates.certificate))
