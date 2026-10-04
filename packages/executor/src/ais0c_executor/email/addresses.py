"""Recipient addresses and the allowed company domains (architecture §9, "E-posta bildirimi").

The executor sends only to addresses in the company's allowed domains (`allowed_email_domains`),
so an e-mail cannot become a way to send data out (D-22). One recipient outside them refuses the
whole e-mail; nobody gets it.

An address is accepted only in its plain ASCII form `local@domain`:

- the local part is a dot-atom: letters, digits and ``!#$%&'*+/=?^_`{|}~-``, with single dots
  between them. Quoted strings, comments, display names and spaces are refused.
- the domain is two or more labels of letters, digits and inner hyphens.
- at most 64 characters before the "@" and 254 in all.

The domain must equal an allowed domain, compared without regard to case. A subdomain is allowed
only if it is listed itself. Anything else is not an address: a second "@", a trailing dot, a
comma that would add a second recipient, a line break that would add a header.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

_ATOM: Final = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LABEL: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DOMAIN: Final = rf"{_LABEL}(?:\.{_LABEL})+"

ADDRESS: Final = re.compile(rf"(?P<local>{_ATOM}(?:\.{_ATOM})*)@(?P<domain>{_DOMAIN})")
DOMAIN: Final = re.compile(_DOMAIN)
MAX_ADDRESS_LENGTH: Final = 254
MAX_LOCAL_LENGTH: Final = 64
MAX_DOMAIN_LENGTH: Final = 253


class RefusalReason(StrEnum):
    NOT_AN_ADDRESS = "not_an_address"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"


@dataclass(frozen=True)
class RefusedRecipient:
    address: str
    reason: RefusalReason


def address_domain(address: str) -> str | None:
    """The domain of `address` in lower case; None if it is not a plain address."""
    if len(address) > MAX_ADDRESS_LENGTH:
        return None
    match = ADDRESS.fullmatch(address)
    if match is None or len(match["local"]) > MAX_LOCAL_LENGTH:
        return None
    return match["domain"].lower()


def normalize_domain(domain: str) -> str | None:
    """An allowed domain as addresses are compared with it: lower case, without surrounding
    spaces. None if it is not a domain name."""
    value = domain.strip().lower()
    if len(value) > MAX_DOMAIN_LENGTH or not DOMAIN.fullmatch(value):
        return None
    return value


def refused_recipients(
    recipients: Iterable[str], allowed_domains: Iterable[str]
) -> list[RefusedRecipient]:
    """The recipients the executor does not send to, in the given order; empty when every one is
    a plain address in an allowed domain. An allowed domain that is not a domain name allows
    nothing."""
    allowed = {domain for domain in map(normalize_domain, allowed_domains) if domain is not None}
    refused: list[RefusedRecipient] = []
    for address in recipients:
        domain = address_domain(address)
        if domain is None:
            refused.append(RefusedRecipient(address, RefusalReason.NOT_AN_ADDRESS))
        elif domain not in allowed:
            refused.append(RefusedRecipient(address, RefusalReason.DOMAIN_NOT_ALLOWED))
    return refused
