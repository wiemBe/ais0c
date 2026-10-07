"""Render a structured :class:`~.templates.Event` to a vendor wire line.

Each line is a complete syslog message: an RFC 3164 priority + timestamp +
hostname header, then the vendor body the matching QRadar DSM parses. The
formats are modelled on real samples:

* FortiGate: the ``key=value`` FortiOS event/traffic format from the FortiOS Log
  Message Reference and IBM's FortiGate DSM sample messages.
* F5 BIG-IP ASM: the ``ASM:key="value",...`` request log of the ASM logging
  profile, as in IBM's F5 BIG-IP ASM DSM sample messages.
* Windows: the WinCollect ``AgentDevice=WindowsLog`` MSEVEN6 format the stock
  Microsoft Windows Security Event Log DSM parses.

Timestamps are rendered in UTC. The functions are pure and take the event's time
from the event, so a seeded run renders byte-for-byte identically.
"""

from __future__ import annotations

from datetime import UTC

from .synthetic import LAB_DOMAIN as _LAB_DOMAIN
from .synthetic import LAB_NETBIOS_DOMAIN as _NETBIOS_DOMAIN
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
    "4771": "Kerberos pre-authentication failed.",
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
    if event_id == "4771":
        return _render_kerberos_preauth_failure(event)
    if event_id == "4625":
        return _render_failed_logon(event)
    message = (
        f"{_WINDOWS_LOGON_MESSAGE[event_id]} "
        f"Subject: Security ID: S-1-0-0 Account Name: - "
        f"New Logon: Security ID: {_sid(event.username)} "
        f"Account Name: {event.username} Account Domain: BANK "
        f"Logon Type: {event.extra['logon_type']} "
        f"Workstation Name: {event.host} Source Network Address: {event.src}"
    )
    fields = [
        *_wincollect_prefix(event, keywords="Audit Success", task="SE_ADT_LOGON_LOGON"),
        f"Message={message}",
    ]
    return _render_wincollect(event, fields)


def _render_failed_logon(event: Event) -> str:
    """4625 in the Security log's own layout.

    The account that failed is under "Account For Which Logon Failed"; the DSM takes the
    username from there. With a 4624-style "New Logon" section the lab parsed the subject's
    "Account Name: -" and left the username empty.
    """
    message = (
        f"{_WINDOWS_LOGON_MESSAGE['4625']} "
        "Subject: Security ID: S-1-0-0 Account Name: - Account Domain: - Logon ID: 0x0 "
        f"Logon Type: {event.extra['logon_type']} "
        "Account For Which Logon Failed: Security ID: S-1-0-0 "
        f"Account Name: {event.username} Account Domain: BANK "
        "Failure Information: Failure Reason: Unknown user name or bad password. "
        "Status: 0xC000006D Sub Status: 0xC000006A "
        "Process Information: Caller Process ID: 0x0 Caller Process Name: - "
        f"Network Information: Workstation Name: {event.host} "
        f"Source Network Address: {event.src} Source Port: 0 "
        "Detailed Authentication Information: Logon Process: NtLmSsp "
        "Authentication Package: NTLM"
    )
    fields = [
        *_wincollect_prefix(event, keywords="Audit Failure", task="SE_ADT_LOGON_LOGON"),
        f"Message={message}",
    ]
    return _render_wincollect(event, fields)


def _render_kerberos_preauth_failure(event: Event) -> str:
    """4771: the domain controller refused a pre-authentication (bad password, 0x18)."""
    message = (
        f"{_WINDOWS_LOGON_MESSAGE['4771']} "
        f"Account Information: Security ID: {_sid(event.username)} "
        f"Account Name: {event.username} "
        "Service Information: Service Name: krbtgt/BANK "
        f"Network Information: Client Address: ::ffff:{event.src} Client Port: 49152 "
        "Additional Information: Ticket Options: 0x40810010 Failure Code: 0x18 "
        "Pre-Authentication Type: 2"
    )
    fields = [
        *_wincollect_prefix(event, keywords="Audit Failure", task="SE_ADT_ACCOUNT_LOGON"),
        f"Message={message}",
    ]
    return _render_wincollect(event, fields)


def render_windows_kerberos_tgs(event: Event) -> str:
    """4769: a service ticket was requested for ``ServiceName``."""
    message = (
        "A Kerberos service ticket was requested. "
        f"Account Information: Account Name: {event.username} "
        f"Account Domain: {_NETBIOS_DOMAIN} "
        "Logon GUID: {00000000-0000-0000-0000-000000000000} "
        f"Service Information: Service Name: {event.extra['service']} "
        f"Service ID: {_sid(event.extra['service'])} "
        f"Network Information: Client Address: ::ffff:{event.src} "
        f"Client Port: {event.extra['client_port']} "
        "Additional Information: Ticket Options: 0x40810000 "
        f"Ticket Encryption Type: {event.extra['encryption_type']} "
        "Failure Code: 0x0 Transited Services: -"
    )
    fields = [
        *_wincollect_prefix(event, keywords="Audit Success", task="SE_ADT_ACCOUNT_LOGON"),
        f"Message={message}",
    ]
    return _render_wincollect(event, fields)


def render_f5_asm(event: Event) -> str:
    """The ASM request log. ``request_status`` carries the policy's decision."""
    moment = event.when.astimezone(UTC)
    stamp = f"{moment:%Y-%m-%d %H:%M:%S}"
    uri = event.extra["uri"]
    query = event.extra["query_string"]
    request = (
        f"{event.extra['method']} {uri}?{query} HTTP/1.1 User-Agent: {event.extra['user_agent']}"
    )
    blocked = event.action == "blocked"
    fields = [
        ("unit_hostname", f"{event.host}.{_LAB_DOMAIN}"),
        ("management_ip_address", "10.10.0.5"),
        ("http_class_name", "/Common/bank_web_policy"),
        ("web_application_name", "/Common/bank_web_policy"),
        ("policy_name", "/Common/bank_web_policy"),
        ("policy_apply_date", "2026-01-01 00:00:00"),
        ("violations", event.extra["violations"]),
        ("support_id", event.extra["support_id"]),
        ("request_status", event.action),
        ("response_code", "0" if blocked else "200"),
        ("ip_client", event.src),
        ("route_domain", "0"),
        ("method", event.extra["method"]),
        ("protocol", "HTTP"),
        ("query_string", query),
        ("x_forwarded_for_header_value", "N/A"),
        ("sig_ids", event.extra["sig_ids"]),
        ("sig_names", event.extra["sig_names"]),
        ("date_time", stamp),
        ("severity", event.extra["severity"]),
        ("attack_type", event.extra["attack_type"]),
        ("geo_location", "N/A"),
        ("ip_address_intelligence", "N/A"),
        ("username", event.username),
        ("session_id", "0"),
        ("src_port", event.extra["src_port"]),
        ("dest_port", "443"),
        ("dest_ip", event.dst),
        ("sub_violations", "N/A"),
        ("virus_name", "N/A"),
        ("violation_rating", event.extra["violation_rating"]),
        ("vs_name", "/Common/bank_web_vs"),
        ("uri", uri),
        ("request", request),
    ]
    body = ",".join(f'{key}="{value}"' for key, value in fields)
    return f"{_rfc3164_header(event)} ASM:{body}"


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
    LogKind.WINDOWS_KERBEROS_TGS: render_windows_kerberos_tgs,
    LogKind.F5_ASM: render_f5_asm,
}


def render(event: Event) -> str:
    """Render ``event`` to its complete syslog wire line."""
    return RENDERERS[event.kind](event)
