"""Render a structured :class:`~.templates.Event` to a vendor wire line.

Each line is a complete syslog message: an RFC 3164 priority + timestamp +
hostname header, then the vendor body the matching QRadar DSM parses. The
formats are modelled on real samples:

* FortiGate: the ``key=value`` FortiOS event/traffic format from the FortiOS Log
  Message Reference and IBM's FortiGate DSM sample messages.
* Windows: the WinCollect ``AgentDevice=WindowsLog`` MSEVEN6 format the stock
  Microsoft Windows Security Event Log DSM parses.

Timestamps are rendered in UTC. The functions are pure and take the event's time
from the event, so a seeded run renders byte-for-byte identically.
"""

from __future__ import annotations

from datetime import UTC

from .templates import Event, LogKind

# Facility 16 (local0) / severity 6 (info) -> PRI 134, as appliances send.
_SYSLOG_PRI = "<134>"


def _rfc3164_header(event: Event) -> str:
    """``<pri>Mon DD HH:MM:SS host`` — the BSD syslog envelope QRadar expects.

    RFC 3164 space-pads the day of month to two columns (``Jan  5``), so the day
    is formatted on its own rather than with ``%d``, which zero-pads.
    """
    moment = event.when.astimezone(UTC)
    stamp = f"{moment:%b} {moment.day:2d} {moment:%H:%M:%S}"
    return f"{_SYSLOG_PRI}{stamp} {event.host}"


def _epoch_ns(event: Event) -> int:
    return int(event.when.astimezone(UTC).timestamp() * 1_000_000_000)


def _epoch_s(event: Event) -> int:
    return int(event.when.astimezone(UTC).timestamp())


def _fortigate_datetime(event: Event) -> tuple[str, str]:
    moment = event.when.astimezone(UTC)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M:%S")


def render_fortigate_vpn(event: Event) -> str:
    date, time = _fortigate_datetime(event)
    up = event.action == "tunnel-up"
    fields = [
        f"date={date}",
        f"time={time}",
        'devname="VPN-GW-01"',
        'devid="FGLAB0000000001"',
        f"eventtime={_epoch_ns(event)}",
        'tz="+0000"',
        f'logid="{event.extra["logid"]}"',
        'type="event"',
        'subtype="vpn"',
        'level="information"',
        'vd="root"',
        f'logdesc="SSL VPN tunnel {"up" if up else "down"}"',
        f'action="{event.action}"',
        'tunneltype="ssl-tunnel"',
        f"tunnelid={event.extra['tunnelid']}",
        f"remip={event.src}",
        f"tunnelip={event.dst}",
        f'srccountry="{event.extra["srccountry"]}"',
        f'user="{event.username}"',
        f'group="{event.extra["group"]}"',
        'dst_host="N/A"',
        f'reason="{"tunnel established" if up else "user requested termination"}"',
        f'msg="SSL tunnel {"established" if up else "shutdown"}"',
    ]
    return f"{_rfc3164_header(event)} {' '.join(fields)}"


def render_fortigate_traffic(event: Event) -> str:
    date, time = _fortigate_datetime(event)
    level = "notice" if event.action == "accept" else "warning"
    fields = [
        f"date={date}",
        f"time={time}",
        'devname="VPN-GW-01"',
        'devid="FGLAB0000000001"',
        f"eventtime={_epoch_ns(event)}",
        'tz="+0000"',
        f'logid="{event.extra["logid"]}"',
        'type="traffic"',
        'subtype="forward"',
        f'level="{level}"',
        'vd="root"',
        f"srcip={event.src}",
        f"srcport={event.extra['srcport']}",
        'srcintf="wan1"',
        f"dstip={event.dst}",
        f"dstport={event.extra['dstport']}",
        'dstintf="internal"',
        f'srccountry="{event.extra["srccountry"]}"',
        f'dstcountry="{event.extra["dstcountry"]}"',
        f"sessionid={event.extra['sessionid']}",
        "proto=6",
        f'action="{event.action}"',
        "policyid=12",
        'policytype="policy"',
        f'service="{event.extra["service"]}"',
        'trandisp="snat"',
    ]
    return f"{_rfc3164_header(event)} {' '.join(fields)}"


# Windows logon event id -> human message, matching the Security log wording.
_WINDOWS_LOGON_MESSAGE = {
    "4624": "An account was successfully logged on.",
    "4625": "An account failed to log on.",
}

# WinCollect separates its name=value fields with a tab, and the QRadar Microsoft
# Windows Security Event Log DSM relies on it: verified on the lab, a tab-delimited
# payload maps to the real QID (e.g. 4624 -> "An account was successfully logged
# on"), while the same fields joined with spaces land as the unparsed "Event 0"
# (qid 0). The RFC 3164 header is still space-separated from the body.
_WINCOLLECT_FIELD_SEP = "\t"


def _render_wincollect(event: Event, fields: list[str]) -> str:
    return f"{_rfc3164_header(event)} {_WINCOLLECT_FIELD_SEP.join(fields)}"


def _wincollect_prefix(event: Event, *, keywords: str, task: str) -> list[str]:
    """The fixed WinCollect MSEVEN6 fields shared by every Windows event."""
    return [
        "AgentDevice=WindowsLog",
        "AgentLogFile=Security",
        "PluginVersion=WC.MSEVEN6.10.1.2.20",
        "Source=Microsoft-Windows-Security-Auditing",
        f"Computer={event.host}.bank.example",
        f"OriginatingComputer={event.src}",
        "User=",
        "Domain=",
        f"EventID={event.extra['event_id']}",
        f"EventIDCode={event.extra['event_id']}",
        "EventType=8",
        f"TimeGenerated={_epoch_s(event)}",
        f"TimeWritten={_epoch_s(event)}",
        "Level=Log Always",
        f"Keywords={keywords}",
        f"Task={task}",
        "Opcode=Info",
    ]


def render_windows_logon(event: Event) -> str:
    event_id = event.extra["event_id"]
    success = event_id == "4624"
    keywords = "Audit Success" if success else "Audit Failure"
    message = (
        f"{_WINDOWS_LOGON_MESSAGE[event_id]} "
        f"Subject: Security ID: S-1-0-0 Account Name: - "
        f"New Logon: Security ID: {_sid(event.username)} "
        f"Account Name: {event.username} Account Domain: BANK "
        f"Logon Type: {event.extra['logon_type']} "
        f"Workstation Name: {event.host} Source Network Address: {event.src}"
    )
    fields = [
        *_wincollect_prefix(event, keywords=keywords, task="SE_ADT_LOGON_LOGON"),
        f"Message={message}",
    ]
    return _render_wincollect(event, fields)


def render_windows_dcsync(event: Event) -> str:
    guid = event.extra["guid"]
    guid_name = event.extra["guid_name"]
    message = (
        "An operation was performed on an object. "
        f"Subject: Security ID: {_sid(event.username)} "
        f"Account Name: {event.username} Account Domain: BANK "
        "Object: Object Server: DS Object Type: domainDNS "
        "Object Name: DC=bank,DC=example Handle ID: 0x0 "
        f"Operation: Operation Type: Object Access Accesses: Control Access "
        f"Access Mask: 0x100 Properties: Control Access {{{guid}}} {guid_name}"
    )
    fields = [
        *_wincollect_prefix(event, keywords="Audit Success", task="SE_ADT_DS_ACCESS"),
        f"Message={message}",
    ]
    return _render_wincollect(event, fields)


def _sid(username: str) -> str:
    """A stable, obviously-synthetic SID derived from the account name.

    Not a real directory SID; it only has to look like one and stay constant
    for a given account so a seeded run renders identically.
    """
    suffix = 1000 + (sum(ord(c) for c in username) % 2000)
    return f"S-1-5-21-1111111111-2222222222-3333333333-{suffix}"


RENDERERS = {
    LogKind.FORTIGATE_VPN: render_fortigate_vpn,
    LogKind.FORTIGATE_TRAFFIC: render_fortigate_traffic,
    LogKind.WINDOWS_LOGON: render_windows_logon,
    LogKind.WINDOWS_DCSYNC: render_windows_dcsync,
}


def render(event: Event) -> str:
    """Render ``event`` to its complete syslog wire line."""
    return RENDERERS[event.kind](event)
