"""Sending e-mails through the company's SMTP relay (architecture §9, "E-posta bildirimi"; D-14).

`SmtpSettings` are configuration (`ais0c_activities.email` reads them from the environment): the
relay's host and port, how the connection is encrypted, the sender address and, when the relay
wants one, a login.

- `starttls` (the default): a plain connection upgraded with STARTTLS before anything is sent.
  A relay that does not offer STARTTLS is refused; the e-mail never goes out in clear text.
- `implicit`: TLS from the first byte (SMTPS, usually port 465).
- `none`: no encryption, for the dev stack's Mailpit and relays without TLS. No login over it.

With TLS the relay's certificate is verified against the system's CAs or, with `ca_file`, against
that file alone (the company's CA).

The e-mail is plain text in UTF-8, quoted-printable. The subject is encoded with the legacy
`email.header.Header`, because the current API folds a long non-ASCII subject so that it decodes
with an extra space. The Message-ID comes from the idempotency key, so every attempt to send the
same e-mail carries the same one, and a mail system that drops duplicate IDs keeps one copy.
`Auto-Submitted` and `X-Auto-Response-Suppress` keep out-of-office replies away.

smtplib blocks, so every connection runs its steps on a thread of its own, one after another;
the timeout bounds each network step.
"""

import asyncio
import hashlib
import re
import smtplib
import ssl
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.charset import QP, Charset
from email.header import Header
from email.message import Message
from email.utils import format_datetime, formataddr
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, FilePath, SecretStr, model_validator

from ais0c_contracts import EmailMessage
from ais0c_executor.common import clean_text
from ais0c_executor.email.addresses import address_domain
from ais0c_executor.email.errors import EmailTransportError, InvalidEmail
from ais0c_executor.email.render import check_subject

SENDER_NAME: Final = "AI-SOC"
# A host name or an IP address; nothing a header or a command line could be built from.
_HOST: Final = re.compile(r"[A-Za-z0-9.:-]{1,253}")
_LOGIN: Final = re.compile(r"[\x21-\x7e]{1,256}")
_REPLY_LENGTH: Final = 200


class TlsMode(StrEnum):
    STARTTLS = "starttls"
    IMPLICIT = "implicit"
    NONE = "none"


DEFAULT_PORTS: Final = {TlsMode.STARTTLS: 587, TlsMode.IMPLICIT: 465, TlsMode.NONE: 25}


class SmtpSettings(BaseModel):
    """How the executor reaches the SMTP relay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    """None: 587 with STARTTLS, 465 with implicit TLS, 25 without TLS."""
    tls: TlsMode = TlsMode.STARTTLS
    sender: str
    """The From address, for example `ai-soc@example.com`; also the Message-ID's domain."""
    username: str | None = None
    password: SecretStr | None = None
    ca_file: FilePath | None = None
    """The CA certificates the relay's certificate is checked against, instead of the
    system's."""
    timeout: Annotated[float, Field(gt=0, le=300)] = 30.0
    """Seconds each network step may take."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not _HOST.fullmatch(self.host):
            raise ValueError("host must be a host name or an IP address")
        if address_domain(self.sender) is None:
            raise ValueError("sender must be a plain address such as ai-soc@example.com")
        if (self.username is None) != (self.password is None):
            raise ValueError("username and password go together")
        if self.username is not None:
            if not _LOGIN.fullmatch(self.username):
                raise ValueError("username must be printable ASCII without spaces")
            if self.tls is TlsMode.NONE:
                raise ValueError("a login needs TLS; the password would go out in clear text")
        if self.ca_file is not None and self.tls is TlsMode.NONE:
            raise ValueError("ca_file needs TLS")
        return self

    @property
    def effective_port(self) -> int:
        return self.port if self.port is not None else DEFAULT_PORTS[self.tls]


@dataclass(frozen=True)
class SendReceipt:
    """What the relay said when it took an e-mail."""

    message_id: str
    refused: tuple[str, ...] = ()
    """Recipients the relay refused while it took the e-mail for the others."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class SmtpTransport:
    """Connections to the relay of `settings`."""

    def __init__(self, settings: SmtpSettings, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._settings = settings
        self._clock = clock

    @property
    def settings(self) -> SmtpSettings:
        return self._settings

    @asynccontextmanager
    async def connect(self) -> AsyncIterator["SmtpConnection"]:
        """A connection that is encrypted and logged in as the settings say.

        Raises `EmailTransportError` if it cannot be opened. Leaving the block sends QUIT after
        the last step, on the connection's own thread.
        """
        worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ais0c-smtp")
        try:
            client = await _run(worker, "connect", self._open)
            try:
                yield SmtpConnection(client, worker, self._settings, self._clock)
            finally:
                # Not awaited: a cancelled send may still be running on the thread, and the QUIT
                # must come after it, not in the middle of it.
                worker.submit(_close, client)
        finally:
            worker.shutdown(wait=False)

    def _open(self) -> smtplib.SMTP:
        settings = self._settings
        context = None
        if settings.tls is not TlsMode.NONE:
            cafile = None if settings.ca_file is None else str(settings.ca_file)
            context = ssl.create_default_context(cafile=cafile)
        if settings.tls is TlsMode.IMPLICIT:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.host, settings.effective_port, timeout=settings.timeout, context=context
            )
        else:
            client = smtplib.SMTP(settings.host, settings.effective_port, timeout=settings.timeout)
        try:
            client.ehlo()
            if settings.tls is TlsMode.STARTTLS:
                # Raises SMTPNotSupportedError if the relay does not offer it: no fallback to
                # clear text.
                client.starttls(context=context)
                client.ehlo()
            if settings.username is not None and settings.password is not None:
                client.login(settings.username, settings.password.get_secret_value())
        except BaseException:
            _close(client)
            raise
        return client


class SmtpConnection:
    """One open connection to the relay."""

    def __init__(
        self,
        client: smtplib.SMTP,
        worker: ThreadPoolExecutor,
        settings: SmtpSettings,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._worker = worker
        self._settings = settings
        self._clock = clock

    async def send(self, message: EmailMessage, body: str) -> SendReceipt:
        """Send `message` with `body` to its recipients.

        Raises `EmailTransportError` when the relay does not take it for any recipient.
        """
        mime = mime_message(message, body, sender=self._settings.sender, sent_at=self._clock())
        sender, recipients = self._settings.sender, list(message.recipients)

        def send() -> Mapping[str, tuple[int, bytes]]:
            return self._client.send_message(mime, from_addr=sender, to_addrs=recipients)

        refused = await _run(self._worker, "send", send)
        return SendReceipt(message_id=str(mime["Message-ID"]), refused=tuple(sorted(refused)))


def mime_message(message: EmailMessage, body: str, *, sender: str, sent_at: datetime) -> Message:
    """`message` as it goes to the relay. Every header is built from checked values only.

    Raises `InvalidEmail` if a recipient or the sender is not a plain address, or the subject
    is not one clean line.
    """
    for address in (sender, *message.recipients):
        if address_domain(address) is None:
            raise InvalidEmail("a recipient or the sender is not a plain address")
    if not message.recipients:
        raise InvalidEmail("an e-mail needs a recipient")
    check_subject(message.subject)
    charset = Charset("utf-8")
    charset.body_encoding = QP
    mime: Message[str, str | Header] = Message()
    mime.set_payload(body, charset)
    mime["From"] = formataddr((SENDER_NAME, sender))
    mime["To"] = ", ".join(message.recipients)
    mime["Subject"] = Header(message.subject, "utf-8", header_name="Subject")
    mime["Date"] = format_datetime(sent_at)
    mime["Message-ID"] = message_id(message.idempotency_key, sender)
    # RFC 3834; Exchange's own header for the same. No out-of-office or other automatic replies.
    mime["Auto-Submitted"] = "auto-generated"
    mime["X-Auto-Response-Suppress"] = "All"
    # Lets operators sort the platform's e-mails with mail rules.
    mime["X-AIS0C-Kind"] = message.kind.value
    return mime


def message_id(idempotency_key: str, sender: str) -> str:
    """The Message-ID of the e-mail with `idempotency_key`: the same on every attempt."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    domain = address_domain(sender)
    if domain is None:
        raise InvalidEmail("the sender is not a plain address")
    return f"<ais0c.{digest}@{domain}>"


def transport_error(step: str, error: Exception) -> EmailTransportError:
    """`error` of the SMTP step `step` as an `EmailTransportError` that says whether a retry
    may succeed: a lost connection or a 4xx reply may, a 5xx reply, a missing extension or a
    certificate that does not verify does not."""
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = sorted({code for code, _ in error.recipients.values()})
        retryable = bool(codes) and all(400 <= code < 500 for code in codes)
        return EmailTransportError(
            f"{step}: the relay refused every recipient (replies {codes})", retryable=retryable
        )
    if isinstance(error, smtplib.SMTPResponseException):
        code = error.smtp_code
        reply = error.smtp_error
        text = reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
        return EmailTransportError(
            f"{step}: the relay answered {code} {clean_text(text, _REPLY_LENGTH)}",
            retryable=not 500 <= code < 600,
        )
    if isinstance(error, smtplib.SMTPNotSupportedError):
        return EmailTransportError(
            f"{step}: the relay does not support what the settings need: {error}",
            retryable=False,
        )
    if isinstance(error, ssl.SSLCertVerificationError):
        reason = getattr(error, "verify_message", None) or error
        return EmailTransportError(
            f"{step}: the relay's certificate does not verify: {reason}", retryable=False
        )
    return EmailTransportError(f"{step}: {type(error).__name__}: {error}", retryable=True)


async def _run[T](worker: ThreadPoolExecutor, step: str, function: Callable[[], T]) -> T:
    try:
        return await asyncio.get_running_loop().run_in_executor(worker, function)
    except (smtplib.SMTPException, OSError) as error:
        raise transport_error(step, error) from error


def _close(client: smtplib.SMTP) -> None:
    try:
        client.quit()
    except (smtplib.SMTPException, OSError):
        client.close()
