"""Synthetic-data discipline for the log generator.

Every value the generator emits must be synthetic: the lab must never receive a
real address, domain, user or host (AGENTS.md hard rule 6). This module is the
single source of truth for what "synthetic" means and the scanner that proves
it, so both the generator (which fails closed before sending) and the tests use
the same rules.

The rules:

* IPv4 literals are either RFC 5737 documentation ranges (for anything standing
  in for the public internet) or RFC 1918 / loopback ranges (for the lab's own
  internal hosts). A real routable address never appears.
* Domains end in a reserved suffix from RFC 2606 / RFC 6761 (``.example``,
  ``example.com`` and friends, ``.test``, ``.invalid``, ``.localhost``).
* Usernames and host names are drawn from the fixed pools below, every one of
  which is obviously a lab value.
"""

from __future__ import annotations

import ipaddress
import re

# --- addresses ----------------------------------------------------------------

#: RFC 5737 documentation ranges. Anything representing the public internet (a
#: VPN remote IP, an external firewall source) is drawn from these.
DOCUMENTATION_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("192.0.2.0/24"),
    ipaddress.IPv4Network("198.51.100.0/24"),
    ipaddress.IPv4Network("203.0.113.0/24"),
)

#: RFC 1918 private ranges plus loopback. The lab's internal hosts (domain
#: controllers, file servers, VPN tunnel addresses) live here.
INTERNAL_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
)

#: Reserved domain suffixes (RFC 2606, RFC 6761). The trailing dot is matched
#: explicitly so "notexample.com" does not pass as "example.com".
RESERVED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".test",
    ".invalid",
    ".localhost",
)

# --- synthetic identities -----------------------------------------------------

#: Internal DNS zone for the fake bank. A reserved TLD, so it can never resolve.
LAB_DOMAIN = "bank.example"
#: NetBIOS-style short domain used in Windows payloads.
LAB_NETBIOS_DOMAIN = "BANK"

#: Human user accounts. Every name is unmistakably a lab value.
#:
#: A name must not read as a DNS name to :func:`assert_text_is_synthetic`: a
#: dotted name whose last label is alphabetic (``analyst.lab``) is scanned as a
#: domain and rejected, because ``.lab`` is not a reserved suffix. So human
#: names either carry no dot or end in a digit (``vpn.user1``), which keeps the
#: realistic ``first.last`` VPN style without tripping the scanner.
SYNTHETIC_USERS: tuple[str, ...] = (
    "vpn.user1",
    "vpn.user2",
    "vpn.user3",
    "analyst1",
    "helpdesk1",
)

#: Service and administrative accounts used by the DCSync scenario.
SYNTHETIC_SERVICE_ACCOUNTS: tuple[str, ...] = (
    "svc_backup",
    "svc_sql",
    "bkupadmin",
)

#: The Azure AD Connect synchronisation account. A real deployment names it
#: ``MSOL_<16 hex>``; this is the benign explanation for replication in H2.
MSOL_ACCOUNT = "MSOL_a1b2c3d4e5f6a7b8"

#: Lab host names. These match the identifiers already present on the lab
#: QRadar log sources (DC-LAB-01@Windows, FILE-SRV-01@windows, ...), so events
#: route to the existing log source instead of auto-creating a new one.
SYNTHETIC_HOSTS: tuple[str, ...] = (
    "DC-LAB-01",
    "DC-LAB-02",
    "FILE-SRV-01",
    "APP-SRV-01",
    "SQL-SRV-01",
    "WEB-SRV-01",
    "VPN-GW-01",
)

#: Machine accounts (end in ``$``). Used as the true-benign DCSync case that the
#: H2 Sigma rule's machine-account filter is meant to exclude.
SYNTHETIC_MACHINE_ACCOUNTS: tuple[str, ...] = (
    "DC-LAB-01$",
    "DC-LAB-02$",
)

#: Every name the generator is allowed to place in a username field.
ALL_SYNTHETIC_ACCOUNTS: frozenset[str] = frozenset(
    (*SYNTHETIC_USERS, *SYNTHETIC_SERVICE_ACCOUNTS, *SYNTHETIC_MACHINE_ACCOUNTS, MSOL_ACCOUNT)
)

#: Every name the generator is allowed to place in a host field. The short host
#: and its fully-qualified form under the lab domain are both allowed.
ALL_SYNTHETIC_HOSTS: frozenset[str] = frozenset(
    (*SYNTHETIC_HOSTS, *(f"{h}.{LAB_DOMAIN}" for h in SYNTHETIC_HOSTS))
)


class SyntheticDataError(AssertionError):
    """Raised when a value that should be synthetic is not.

    Subclasses ``AssertionError`` so it reads naturally in a test, but the
    generator also raises it at runtime to fail closed before sending.
    """


# A token that looks like an IPv4 address: four dotted decimal groups. Matched
# loosely, then validated with ipaddress, so "999.1.1.1" is reported rather
# than silently skipped.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# A token that looks like a DNS name: at least one dot, letters in the TLD.
_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,62})\.)+[a-zA-Z]{2,63}\b")


def ip_is_synthetic(value: str) -> bool:
    """True if ``value`` is a documentation or internal/loopback IPv4 address."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not isinstance(address, ipaddress.IPv4Address):
        return False
    return any(address in net for net in (*DOCUMENTATION_NETWORKS, *INTERNAL_NETWORKS))


def domain_is_synthetic(value: str) -> bool:
    """True if ``value`` ends in a reserved domain suffix."""
    lowered = value.lower()
    return any(
        lowered == suffix.lstrip(".") or lowered.endswith(suffix)
        for suffix in RESERVED_DOMAIN_SUFFIXES
    )


def assert_text_is_synthetic(text: str, *, where: str) -> None:
    """Scan a blob of generated text and reject any non-synthetic literal.

    ``text`` is the full wire payload (or any generated string). Every IPv4
    literal must be documentation or internal, and every domain-like token must
    end in a reserved suffix. ``where`` names the source for the error message.
    """
    for match in _IPV4_RE.finditer(text):
        token = match.group(0)
        if not ip_is_synthetic(token):
            raise SyntheticDataError(f"{where}: non-synthetic IPv4 address {token!r}")
    for match in _DOMAIN_RE.finditer(text):
        token = match.group(0)
        # A dotted-decimal IP also matches the domain pattern; it was already
        # checked above, so skip anything that parses as an address.
        try:
            ipaddress.ip_address(token)
            continue
        except ValueError:
            pass
        if not domain_is_synthetic(token):
            raise SyntheticDataError(f"{where}: non-synthetic domain {token!r}")


def assert_account_is_synthetic(name: str, *, where: str) -> None:
    """Reject a username that is not in the synthetic account pools."""
    if name not in ALL_SYNTHETIC_ACCOUNTS:
        raise SyntheticDataError(f"{where}: non-synthetic account {name!r}")


def assert_host_is_synthetic(name: str, *, where: str) -> None:
    """Reject a host name that is not in the synthetic host pool."""
    if name not in ALL_SYNTHETIC_HOSTS:
        raise SyntheticDataError(f"{where}: non-synthetic host {name!r}")
