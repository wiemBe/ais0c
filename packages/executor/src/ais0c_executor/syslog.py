"""Health alarms as RFC 5424 syslog messages to QRadar (architecture §26, T-23, T-68 (5), T-032).

A health alarm goes out on two channels: an e-mail to the platform team (`ais0c_executor.email`)
and a syslog message, which a QRadar rule catches (the user sets that rule up). The message is a
fixed template, never a model's text:

    <PRI>1 <UTC time> - ais0c - <alarm kind> [ais0c@32473 kind="..." subject="..." ...] ...

- The application name is `ais0c`, the message ID the alarm kind.
- The structured data holds the kind, the subject (and its name, when it has one), the state
  (`open`, `reminder` or `resolved`), the notification number, and the alarm's numbers. Every
  value goes through `clean_text` and the escaping RFC 5424 §6.3.3 asks for (`"`, `\\`, `]`), so
  a log source's name from QRadar cannot end the element early, add an element or a line.
- The message text is a fixed English sentence.
- Severity follows the state: an opened alarm is an error, a reminder a warning, a resolved one a
  notice. The facility is local0.

`SyslogSender.send` sends one message over UDP, or over TCP with octet-counting framing
(RFC 6587 §3.4.1), which a payload that holds a line break cannot break either. The sender holds
no secret. It raises `SyslogError` when the message cannot be delivered; the caller logs it and
goes on, since the alarm's state is recorded either way.
"""

import asyncio
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ais0c_executor.common import clean_text
from ais0c_executor.email.request import HealthAlarm

# The private enterprise number RFC 5612 reserves for documentation: the SD-ID's `@` part.
STRUCTURED_DATA_ID: Final = "ais0c@32473"
APP_NAME: Final = "ais0c"
NIL: Final = "-"
FACILITY_LOCAL0: Final = 16
SEVERITY: Final = {"open": 3, "reminder": 4, "resolved": 5}
DEFAULT_PORT: Final = 514
# One UDP datagram; a message is far below it, the cut only guards the escaping's growth.
MAX_MESSAGE_BYTES: Final = 2048
_VALUE_LENGTH: Final = 200
_HOST: Final = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.:-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")


class SyslogProtocol(StrEnum):
    UDP = "udp"
    TCP = "tcp"


class SyslogError(RuntimeError):
    """The syslog message could not be delivered."""


class SyslogSettings(BaseModel):
    """Where alarms go. Without settings syslog is off."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(min_length=1, max_length=253, pattern=f"^(?:{_HOST.pattern})$")
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    protocol: SyslogProtocol = SyslogProtocol.UDP
    timeout: float = Field(default=5.0, gt=0, le=60)
    """Seconds a TCP connect or send may take."""


def format_message(alarm: HealthAlarm, *, now: datetime) -> bytes:
    """The RFC 5424 message of one notification of `alarm`, UTF-8, without framing."""
    priority = FACILITY_LOCAL0 * 8 + SEVERITY[alarm.status]
    timestamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    params: list[tuple[str, str]] = [
        ("kind", alarm.alarm_kind),
        ("subject", alarm.subject),
        ("status", alarm.status),
        ("notification", str(alarm.notification_no)),
        ("alarm_id", alarm.alarm_id),
    ]
    if alarm.subject_name is not None:
        params.append(("subject_name", alarm.subject_name))
    params.extend(sorted((name, str(value)) for name, value in alarm.counts.items()))
    data = " ".join(f'{name}="{_escape(value)}"' for name, value in params)
    text = f"ais0c health alarm {alarm.status}: {alarm.alarm_kind}"
    message = (
        f"<{priority}>1 {timestamp} {NIL} {APP_NAME} {NIL} {alarm.alarm_kind} "
        f"[{STRUCTURED_DATA_ID} {data}] {text}"
    )
    # A cut inside a multi-byte character would leave invalid UTF-8; drop what is left of it.
    return message.encode("utf-8")[:MAX_MESSAGE_BYTES].decode("utf-8", "ignore").encode("utf-8")


def frame(message: bytes) -> bytes:
    """`message` with TCP's octet-counting frame: its length in bytes, a space, the message."""
    return f"{len(message)} ".encode("ascii") + message


def _escape(value: str) -> str:
    """A PARAM-VALUE: one clean line with `"`, `\\` and `]` escaped by a backslash."""
    cleaned = clean_text(value, _VALUE_LENGTH)
    return "".join(f"\\{char}" if char in '"\\]' else char for char in cleaned)


class SyslogSender:
    """Sends alarm messages to the syslog server `settings` names."""

    def __init__(self, settings: SyslogSettings) -> None:
        self._settings = settings

    async def send(self, alarm: HealthAlarm, *, now: datetime | None = None) -> None:
        """Send one message for `alarm`. Raises `SyslogError` when it cannot be delivered; UDP
        has no acknowledgement, so it fails only when the datagram cannot be sent."""
        message = format_message(alarm, now=datetime.now(UTC) if now is None else now)
        settings = self._settings
        try:
            if settings.protocol is SyslogProtocol.UDP:
                await self._send_udp(message)
            else:
                await asyncio.wait_for(self._send_tcp(frame(message)), settings.timeout)
        except (OSError, TimeoutError) as error:
            raise SyslogError(
                f"syslog to {settings.host}:{settings.port}/{settings.protocol.value}: "
                f"{type(error).__name__}"
            ) from None

    async def _send_udp(self, message: bytes) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=(self._settings.host.strip("[]"), self._settings.port),
        )
        try:
            transport.sendto(message)
        finally:
            transport.close()

    async def _send_tcp(self, framed: bytes) -> None:
        _, writer = await asyncio.open_connection(
            self._settings.host.strip("[]"), self._settings.port
        )
        try:
            writer.write(framed)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
