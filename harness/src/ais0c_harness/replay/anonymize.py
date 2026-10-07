"""Anonymization of a recording (T-052 criterion 2, decision T-70).

What a lab recording holds goes through `Anonymizer` before anything is written:

- every IPv4 address, in any string (a payload included), becomes an address of the documentation
  ranges RFC 5737 (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24), every IPv6 address one of
  2001:db8::/32. The mapping is deterministic: the distinct addresses of the whole recording are
  sorted and handed the ranges' addresses in order, so the same address gets the same
  replacement everywhere in a recording, no two addresses share one, and the same recording
  anonymizes the same way again. An address that is a documentation address already stays.
  Subnet structure is not kept;
- every lab host name becomes `host-<nn>` and every lab domain `corp.example.com`, whatever the
  letter case (`DC-LAB-01.bank.example` becomes `host-01.corp.example.com`). A domain's
  distinguished-name form (`DC=bank,DC=example`) is replaced as well.

The mapping table lives in the object and is never written. `foreign_addresses` is the check the
repository test and the recorder's last step use: the addresses of a text that are not documentation
addresses.
"""

import ipaddress
import re
import string
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from pydantic import JsonValue

IPV4_RANGES: Final = tuple(
    ipaddress.IPv4Network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
IPV6_RANGE: Final = ipaddress.IPv6Network("2001:db8::/32")
DOMAIN: Final = "corp.example.com"
HOST_FORMAT: Final = "host-{:02d}"

# Looser than the replacement needs on purpose: an address inside a longer token still counts.
_IPV4: Final = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_IPV6: Final = re.compile(r"(?<![0-9A-Za-z:])[0-9A-Fa-f:]{2,}(?:\.\d{1,3}){0,3}(?![0-9A-Za-z:])")
_NAME_EDGE_BEFORE: Final = r"(?<![A-Za-z0-9-])"
_NAME_EDGE_AFTER: Final = r"(?![A-Za-z0-9-])"


class AnonymizationError(ValueError):
    """The recording cannot be anonymized (more addresses than the ranges hold)."""


def _ipv4(text: str) -> ipaddress.IPv4Address | None:
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        return None


def _ipv6(text: str) -> ipaddress.IPv6Address | None:
    # A bare "::" is a separator in a log source name, not the unspecified address.
    if text.count(":") < 2 or not any(char in string.hexdigits for char in text):
        return None
    try:
        return ipaddress.IPv6Address(text)
    except ValueError:
        return None


def addresses(text: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every IPv4 and IPv6 address in `text`, in order of appearance."""
    found: list[tuple[int, ipaddress.IPv4Address | ipaddress.IPv6Address]] = []
    for match in _IPV4.finditer(text):
        if (address := _ipv4(match.group())) is not None:
            found.append((match.start(), address))
    for match in _IPV6.finditer(text):
        if (v6 := _ipv6(match.group())) is not None:
            found.append((match.start(), v6))
    return [address for _, address in sorted(found, key=lambda item: item[0])]


def is_documentation(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in IPV4_RANGES)
    return address in IPV6_RANGE


def foreign_addresses(text: str) -> list[str]:
    """The addresses in `text` that are not RFC 5737 or 2001:db8::/32 addresses."""
    return [str(address) for address in addresses(text) if not is_documentation(address)]


def strings(value: JsonValue) -> Iterable[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)


class Anonymizer:
    """Collects the addresses of everything a recording holds, then replaces them."""

    def __init__(self, *, domains: Sequence[str] = (), hosts: Sequence[str] = ()) -> None:
        """`domains` and `hosts` are the lab's own names, as they appear in the data."""
        self._domains = [domain.lower() for domain in domains]
        self._hosts = {
            host.lower(): HOST_FORMAT.format(number) for number, host in enumerate(hosts, 1)
        }
        self._seen4: set[ipaddress.IPv4Address] = set()
        self._seen6: set[ipaddress.IPv6Address] = set()
        self._mapping: dict[str, str] = {}
        self._frozen = False

    def collect(self, text: str) -> None:
        """Note the addresses in `text`; no replacement is possible until `freeze`."""
        if self._frozen:
            raise AnonymizationError("addresses are collected before the mapping is made")
        for address in addresses(text):
            if isinstance(address, ipaddress.IPv4Address):
                self._seen4.add(address)
            else:
                self._seen6.add(address)

    def collect_all(self, value: JsonValue) -> None:
        for text in strings(value):
            self.collect(text)

    def freeze(self) -> None:
        """Make the mapping from everything collected. A documentation address stays as it is
        (a recording is anonymized once, and a fixture written by hand keeps its addresses); the
        others take the documentation addresses nobody uses yet, in order."""
        fixed4 = {address for address in self._seen4 if is_documentation(address)}
        pool4 = [
            address
            for network in IPV4_RANGES
            for address in network.hosts()
            if address not in fixed4
        ]
        moving4 = sorted(self._seen4 - fixed4)
        if len(moving4) > len(pool4):
            raise AnonymizationError(
                f"{len(self._seen4)} IPv4 addresses, the documentation ranges hold "
                f"{len(pool4) + len(fixed4)}"
            )
        for address in fixed4:
            self._mapping[str(address)] = str(address)
        for address, replacement in zip(moving4, pool4, strict=False):
            self._mapping[str(address)] = str(replacement)
        fixed6 = {address for address in self._seen6 if is_documentation(address)}
        for address6 in fixed6:
            self._mapping[str(address6)] = str(address6)
        number = 0
        for address6 in sorted(self._seen6 - fixed6):
            number += 1
            while IPV6_RANGE.network_address + number in fixed6:
                number += 1
            self._mapping[str(address6)] = str(IPV6_RANGE.network_address + number)
        self._frozen = True

    def text(self, value: str) -> str:
        """`value` with its addresses and lab names replaced."""
        if not self._frozen:
            raise AnonymizationError("freeze the mapping before replacing")
        # IPv6 first: ::ffff:192.0.2.1 holds an IPv4 address and is replaced as a whole.
        out = _IPV6.sub(lambda match: self._replacement(match.group()), value)
        out = _IPV4.sub(lambda match: self._replacement(match.group()), out)
        return self._names(out)

    def value(self, value: JsonValue) -> JsonValue:
        """A JSON value with every string (and key) anonymized."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        return value

    def _replacement(self, token: str) -> str:
        address = _ipv4(token) or _ipv6(token)
        if address is None:
            return token
        return self._mapping.get(str(address), token)

    def _names(self, value: str) -> str:
        for domain in self._domains:
            labels = domain.split(".")
            distinguished = ",".join(f"DC={label}" for label in labels)
            value = re.sub(re.escape(distinguished), "DC=corp,DC=example,DC=com", value, flags=re.I)
            value = re.sub(
                f"{_NAME_EDGE_BEFORE}{re.escape(domain)}{_NAME_EDGE_AFTER}",
                DOMAIN,
                value,
                flags=re.I,
            )
        for host, replacement in self._hosts.items():
            value = re.sub(
                f"{_NAME_EDGE_BEFORE}{re.escape(host)}{_NAME_EDGE_AFTER}",
                replacement,
                value,
                flags=re.I,
            )
        return value

    @property
    def mapping_size(self) -> int:
        """How many addresses were mapped; the table itself is not exposed."""
        return len(self._mapping)


def anonymized(
    documents: Mapping[str, JsonValue], *, domains: Sequence[str] = (), hosts: Sequence[str] = ()
) -> tuple[Anonymizer, dict[str, JsonValue]]:
    """Anonymize several documents with one mapping, so an address means the same in each."""
    anonymizer = Anonymizer(domains=domains, hosts=hosts)
    for document in documents.values():
        anonymizer.collect_all(document)
    anonymizer.freeze()
    return anonymizer, {name: anonymizer.value(item) for name, item in documents.items()}
