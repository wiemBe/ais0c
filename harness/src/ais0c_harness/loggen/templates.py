"""Event templates: the four kinds of log the first scenarios need.

A template turns a scenario step's parameters and a seeded RNG into a structured
:class:`Event`. The vendor wire format lives in :mod:`.formats`; a template only
decides *what* happened (which user, from which address, allowed or denied),
never how the bytes look. Keeping the two apart means a scenario can be read
without knowing FortiGate or WinCollect syntax, and a format can be checked
without replaying a scenario.

Two real vendors cover the three scenarios:

* **FortiGate** (``fortigate_vpn``, ``fortigate_traffic``) — one appliance does
  SSL VPN and firewalling, and its logs carry ``srccountry`` as an explicit
  field, which is how a scenario expresses "a country never seen before"
  without relying on GeoIP over synthetic documentation addresses.
* **Microsoft Windows Security Event Log via WinCollect** (``windows_logon``,
  ``windows_dcsync``) — the stock QRadar DSM parses this, and event 4662 with
  the directory-replication access rights is the H2 DCSync signal.
"""

from __future__ import annotations

import enum
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from . import synthetic

# Directory-replication control-access rights. A non-machine account using these
# on a domain controller is the DCSync signal (hunt pack H2 / T1003.006). The
# GUIDs are real and identical to the hunt pack's Sigma rule.
DS_REPLICATION_GUIDS: dict[str, str] = {
    "DS-Replication-Get-Changes": "1131f6aa-9c07-11d1-f79f-00c04fc2dcd2",
    "DS-Replication-Get-Changes-All": "1131f6ad-9c07-11d1-f79f-00c04fc2dcd2",
    "DS-Replication-Get-Changes-In-Filtered-Set": "89e95b76-444d-4c62-991a-0facbeda640c",
}


class LogKind(enum.StrEnum):
    """A log source kind, matching one template and one QRadar DSM."""

    FORTIGATE_VPN = "fortigate_vpn"
    FORTIGATE_TRAFFIC = "fortigate_traffic"
    WINDOWS_LOGON = "windows_logon"
    WINDOWS_DCSYNC = "windows_dcsync"


@dataclass(frozen=True)
class DsmBinding:
    """How QRadar is expected to parse a log kind. Documented in the README."""

    log_source_type: str
    log_source_type_id: int


#: The lab QRadar DSM each kind targets. The type ids were read from the lab
#: (``.../log_source_types``) and are recorded here so the README and the labels
#: agree with what is actually installed.
DSM_BINDINGS: dict[LogKind, DsmBinding] = {
    LogKind.FORTIGATE_VPN: DsmBinding("Fortinet FortiGate Security Gateway", 73),
    LogKind.FORTIGATE_TRAFFIC: DsmBinding("Fortinet FortiGate Security Gateway", 73),
    LogKind.WINDOWS_LOGON: DsmBinding("Microsoft Windows Security Event Log", 12),
    LogKind.WINDOWS_DCSYNC: DsmBinding("Microsoft Windows Security Event Log", 12),
}


@dataclass(frozen=True)
class Event:
    """A single structured event, before it is rendered to a vendor format.

    ``extra`` carries format-specific fields (FortiGate ``logid``, the Windows
    ``EventID`` and replication GUID, and so on). Rendering in :mod:`.formats`
    reads only the fields its kind needs.
    """

    kind: LogKind
    when: datetime
    host: str
    #: Externally visible source address (documentation range) or internal
    #: source, depending on the kind.
    src: str
    #: Destination / internal address.
    dst: str
    username: str
    action: str
    extra: Mapping[str, str] = field(default_factory=dict)


# --- helpers ------------------------------------------------------------------


def _require(params: Mapping[str, object], key: str) -> object:
    if key not in params:
        raise ValueError(f"template parameter {key!r} is required")
    return params[key]


def _str_list(params: Mapping[str, object], key: str) -> list[str]:
    value = _require(params, key)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise ValueError(f"template parameter {key!r} must be a non-empty list of strings")
    return [str(v) for v in value]


def _pick_user(rng: random.Random, params: Mapping[str, object]) -> str:
    """Choose a username from the step's ``users`` pool, refusing real names."""
    user = rng.choice(_str_list(params, "users"))
    synthetic.assert_account_is_synthetic(user, where="scenario users")
    return user


def _pick_host(rng: random.Random, params: Mapping[str, object], key: str = "hosts") -> str:
    host = rng.choice(_str_list(params, key))
    synthetic.assert_host_is_synthetic(host, where=f"scenario {key}")
    return host


def _doc_ip(rng: random.Random, params: Mapping[str, object], key: str) -> str:
    """Resolve an external address: an explicit pool if given, else a random
    documentation-range address."""
    if key in params:
        value = rng.choice(_str_list(params, key))
        synthetic.assert_text_is_synthetic(value, where=f"scenario {key}")
        return value
    net = rng.choice(synthetic.DOCUMENTATION_NETWORKS)
    host_part = rng.randint(1, int(net.num_addresses) - 2)
    return str(net.network_address + host_part)


def _tunnel_ip(rng: random.Random) -> str:
    return f"10.20.{rng.randint(0, 7)}.{rng.randint(2, 254)}"


# --- templates ----------------------------------------------------------------


def build_fortigate_vpn(rng: random.Random, params: Mapping[str, object], when: datetime) -> Event:
    """An SSL VPN tunnel-up/down event (FortiOS logid 0101039947 / ...948)."""
    action = str(params.get("action", "tunnel-up"))
    if action not in {"tunnel-up", "tunnel-down"}:
        raise ValueError("fortigate_vpn action must be 'tunnel-up' or 'tunnel-down'")
    country = str(_require(params, "srccountry"))
    user = _pick_user(rng, params)
    group = str(params.get("group", "sslvpn-users"))
    logid = "0101039947" if action == "tunnel-up" else "0101039948"
    return Event(
        kind=LogKind.FORTIGATE_VPN,
        when=when,
        host="VPN-GW-01",
        src=_doc_ip(rng, params, "remip_pool"),
        dst=_tunnel_ip(rng),
        username=user,
        action=action,
        extra={
            "logid": logid,
            "srccountry": country,
            "group": group,
            "tunnelid": str(rng.randint(1_000_000, 9_999_999)),
        },
    )


def build_fortigate_traffic(
    rng: random.Random, params: Mapping[str, object], when: datetime
) -> Event:
    """A forward-traffic firewall decision (FortiOS logid 0000000013)."""
    action = str(params.get("action", "accept"))
    if action not in {"accept", "deny"}:
        raise ValueError("fortigate_traffic action must be 'accept' or 'deny'")
    country = str(params.get("srccountry", "Reserved"))
    service = str(params.get("service", "HTTPS"))
    dst_port = int(str(params.get("dstport", 443)))
    dst = f"10.10.{rng.randint(0, 7)}.{rng.randint(2, 254)}"
    return Event(
        kind=LogKind.FORTIGATE_TRAFFIC,
        when=when,
        host="VPN-GW-01",
        src=_doc_ip(rng, params, "srcip_pool"),
        dst=dst,
        username="N/A",
        action=action,
        extra={
            "logid": "0000000013",
            "srccountry": country,
            "dstcountry": "Reserved",
            "service": service,
            "srcport": str(rng.randint(1024, 65535)),
            "dstport": str(dst_port),
            "sessionid": str(rng.randint(1_000_000, 99_999_999)),
        },
    )


def build_windows_logon(rng: random.Random, params: Mapping[str, object], when: datetime) -> Event:
    """A Windows logon event (4624 success / 4625 failure)."""
    event_id = str(params.get("event_id", "4624"))
    if event_id not in {"4624", "4625"}:
        raise ValueError("windows_logon event_id must be '4624' or '4625'")
    logon_type = str(params.get("logon_type", "3"))
    user = _pick_user(rng, params)
    host = _pick_host(rng, params)
    src = _doc_ip(rng, params, "src_pool") if "src_pool" in params else _internal_src(rng)
    return Event(
        kind=LogKind.WINDOWS_LOGON,
        when=when,
        host=host,
        src=src,
        dst=host,
        username=user,
        action="success" if event_id == "4624" else "failure",
        extra={"event_id": event_id, "logon_type": logon_type},
    )


def build_windows_dcsync(rng: random.Random, params: Mapping[str, object], when: datetime) -> Event:
    """A directory-service-access event (4662) using replication rights."""
    account_kind = str(_require(params, "account_kind"))
    user = _dcsync_account(rng, account_kind)
    host = _pick_host(rng, params)
    guid_name = rng.choice(list(DS_REPLICATION_GUIDS))
    return Event(
        kind=LogKind.WINDOWS_DCSYNC,
        when=when,
        host=host,
        src=_internal_src(rng),
        dst=host,
        username=user,
        action="replication",
        extra={
            "event_id": "4662",
            "guid_name": guid_name,
            "guid": DS_REPLICATION_GUIDS[guid_name],
            "account_kind": account_kind,
        },
    )


def _internal_src(rng: random.Random) -> str:
    return f"192.168.10.{rng.randint(2, 254)}"


def _dcsync_account(rng: random.Random, account_kind: str) -> str:
    """Resolve the subject account for a 4662 event by its labelled kind."""
    if account_kind == "non_machine":
        return rng.choice(synthetic.SYNTHETIC_SERVICE_ACCOUNTS)
    if account_kind == "msol":
        return synthetic.MSOL_ACCOUNT
    if account_kind == "machine":
        return rng.choice(synthetic.SYNTHETIC_MACHINE_ACCOUNTS)
    raise ValueError("windows_dcsync account_kind must be 'non_machine', 'msol' or 'machine'")


#: A template builder: (rng, step params, event time) -> structured event.
Builder = Callable[[random.Random, Mapping[str, object], datetime], Event]

TEMPLATES: dict[LogKind, Builder] = {
    LogKind.FORTIGATE_VPN: build_fortigate_vpn,
    LogKind.FORTIGATE_TRAFFIC: build_fortigate_traffic,
    LogKind.WINDOWS_LOGON: build_windows_logon,
    LogKind.WINDOWS_DCSYNC: build_windows_dcsync,
}
