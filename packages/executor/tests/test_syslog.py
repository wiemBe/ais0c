"""The RFC 5424 syslog message of a health alarm and its delivery (T-032 criterion 7)."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from email_payloads import ALARM_ID, health_alarm

from ais0c_executor.syslog import (
    SyslogError,
    SyslogProtocol,
    SyslogSender,
    SyslogSettings,
    format_message,
    frame,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 2, 11, 5, 7, 123456, tzinfo=UTC)
# <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD] MSG
HEADER = re.compile(
    r"<(?P<pri>\d{1,3})>1 (?P<time>\S+) (?P<host>\S+) (?P<app>\S+) (?P<proc>\S+) (?P<msgid>\S+)"
    r" (?P<rest>.*)",
    re.DOTALL,
)
# One structured data element: `[ID name="value" ...]`, a value escaping `"`, `\` and `]`.
ELEMENT = re.compile(r'\[ais0c@32473((?: [a-z_]+="(?:[^"\\\]]|\\["\\\]])*")+)\]')


def parse(message: bytes) -> tuple[dict[str, str], dict[str, str], str]:
    """(header fields, structured data parameters, message text); fails if the message is not
    well-formed RFC 5424 with exactly one element."""
    match = HEADER.fullmatch(message.decode("utf-8"))
    assert match is not None
    rest = match["rest"]
    element = ELEMENT.match(rest)
    assert element is not None, rest
    params = {
        name: re.sub(r'\\(["\\\]])', r"\1", value)
        for name, value in re.findall(r'([a-z_]+)="((?:[^"\\]|\\.)*)"', element[1])
    }
    return match.groupdict(), params, rest[element.end() :].lstrip(" ")


def test_the_message_is_rfc_5424_from_a_fixed_template() -> None:
    header, params, text = parse(format_message(health_alarm(), now=NOW))

    assert header["pri"] == str(16 * 8 + 3)  # local0, error: an alarm that opened
    assert header["time"] == "2026-10-02T11:05:07.123Z"
    assert (header["host"], header["app"], header["proc"]) == ("-", "ais0c", "-")
    assert header["msgid"] == "log_source_silent"
    assert params == {
        "kind": "log_source_silent",
        "subject": "17",
        "status": "open",
        "notification": "1",
        "alarm_id": ALARM_ID,
        "subject_name": "FW-DMZ-01",
        "silent_minutes": "75",
        "threshold": "60",
    }
    assert text == "ais0c health alarm open: log_source_silent"


@pytest.mark.parametrize(("status", "pri"), [("open", 131), ("reminder", 132), ("resolved", 133)])
def test_severity_follows_the_state(status: str, pri: int) -> None:
    header, _, text = parse(format_message(health_alarm(status=status), now=NOW))

    assert header["pri"] == str(pri)
    assert status in text


def test_values_from_qradar_cannot_break_the_structured_data() -> None:
    """CR/LF, `]` and `"` in a log source's name stay inside their value (negative test)."""
    nasty = 'FW\r\n<13>1 forged] [x@1 a="b"\\ end"'
    message = format_message(health_alarm(subject_name=nasty, subject='1"]\n2'), now=NOW)

    assert b"\r" not in message
    assert b"\n" not in message
    _, params, text = parse(message)
    # Exactly the parameters of the template, whatever the name holds.
    assert set(params) == {
        "kind",
        "subject",
        "status",
        "notification",
        "alarm_id",
        "subject_name",
        "silent_minutes",
        "threshold",
    }
    assert params["subject_name"] == 'FW <13>1 forged] [x@1 a="b"\\ end"'
    assert params["subject"] == '1"] 2'
    assert text == "ais0c health alarm open: log_source_silent"


def test_a_long_name_is_cut_and_the_message_stays_below_one_datagram() -> None:
    message = format_message(health_alarm(subject_name="é" * 2000), now=NOW)

    assert len(message) < 2048
    message.decode("utf-8")


def test_tcp_framing_counts_octets() -> None:
    message = format_message(health_alarm(subject_name="Güvenlik duvarı"), now=NOW)
    framed = frame(message)

    length, _, payload = framed.partition(b" ")
    assert int(length) == len(payload) == len(message)
    assert payload == message


@asynccontextmanager
async def udp_listener() -> AsyncIterator[tuple[int, asyncio.Queue[bytes]]]:
    received: asyncio.Queue[bytes] = asyncio.Queue()

    class Listener(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: object) -> None:
            received.put_nowait(data)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(Listener, local_addr=("127.0.0.1", 0))
    try:
        yield transport.get_extra_info("sockname")[1], received
    finally:
        transport.close()


@asynccontextmanager
async def tcp_listener() -> AsyncIterator[tuple[int, asyncio.Queue[bytes]]]:
    received: asyncio.Queue[bytes] = asyncio.Queue()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.put_nowait(await reader.read())
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], received
    finally:
        server.close()
        await server.wait_closed()


async def test_udp_delivers_one_datagram() -> None:
    async with udp_listener() as (port, received):
        sender = SyslogSender(SyslogSettings(host="127.0.0.1", port=port))

        await sender.send(health_alarm(), now=NOW)
        datagram = await asyncio.wait_for(received.get(), 5)

    assert datagram == format_message(health_alarm(), now=NOW)
    parse(datagram)


async def test_tcp_delivers_an_octet_counted_frame() -> None:
    async with tcp_listener() as (port, received):
        settings = SyslogSettings(host="127.0.0.1", port=port, protocol=SyslogProtocol.TCP)

        await SyslogSender(settings).send(health_alarm(subject_name="a\nb"), now=NOW)
        data = await asyncio.wait_for(received.get(), 5)

    length, _, payload = data.partition(b" ")
    assert int(length) == len(payload)
    parse(payload)
    assert parse(payload)[1]["subject_name"] == "a b"


async def test_tcp_to_a_closed_port_raises_a_syslog_error() -> None:
    async with tcp_listener() as (port, _):
        pass
    settings = SyslogSettings(host="127.0.0.1", port=port, protocol=SyslogProtocol.TCP, timeout=2)

    with pytest.raises(SyslogError, match=r"127\.0\.0\.1"):
        await SyslogSender(settings).send(health_alarm(), now=NOW)


@pytest.mark.parametrize(
    "changes",
    [{"host": ""}, {"host": "bad host"}, {"host": "a;b"}, {"port": 0}, {"port": 70000}],
)
def test_invalid_settings_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        SyslogSettings.model_validate({"host": "syslog.example.com"} | changes)


def test_the_defaults_are_udp_on_514() -> None:
    settings = SyslogSettings(host="syslog.example.com")

    assert (settings.port, settings.protocol) == (514, SyslogProtocol.UDP)
