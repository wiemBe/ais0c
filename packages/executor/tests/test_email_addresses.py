"""The allowed domain check (T-020 criterion 3): only plain addresses in an allowed company
domain get an e-mail."""

import pytest

from ais0c_executor.email import (
    RefusalReason,
    RefusedRecipient,
    address_domain,
    normalize_domain,
    refused_recipients,
)

ALLOWED = ["example.com", "Bank.Example.ORG"]
NOT_AN_ADDRESS = RefusalReason.NOT_AN_ADDRESS
DOMAIN_NOT_ALLOWED = RefusalReason.DOMAIN_NOT_ALLOWED


@pytest.mark.parametrize(
    "address",
    [
        "soc-1@example.com",
        "SOC-1@EXAMPLE.COM",
        "first.last+alerts@example.com",
        "o'brien@example.com",
        "ops@bank.example.org",
        "a" * 64 + "@example.com",
    ],
)
def test_a_plain_address_in_an_allowed_domain_passes(address: str) -> None:
    assert refused_recipients([address], ALLOWED) == []


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("soc@example.net", DOMAIN_NOT_ALLOWED),
        # A subdomain is not the domain, and neither is a domain that ends like it.
        ("soc@mail.example.com", DOMAIN_NOT_ALLOWED),
        ("soc@example.com.example.net", DOMAIN_NOT_ALLOWED),
        ("soc@notexample.com", DOMAIN_NOT_ALLOWED),
        # Forms that could reach a second recipient or add a header.
        ("soc@example.com, x@example.net", NOT_AN_ADDRESS),
        ("soc@example.com;x@example.net", NOT_AN_ADDRESS),
        ("soc@example.com\r\nBcc: x@example.net", NOT_AN_ADDRESS),
        ("soc@example.com\nBcc: x@example.net", NOT_AN_ADDRESS),
        ("AI-SOC <soc@example.com>", NOT_AN_ADDRESS),
        ("<soc@example.com>", NOT_AN_ADDRESS),
        ('"x@example.net"@example.com', NOT_AN_ADDRESS),
        ("soc(comment)@example.com", NOT_AN_ADDRESS),
        ("soc@example.com@example.net", NOT_AN_ADDRESS),
        ("soc@@example.com", NOT_AN_ADDRESS),
        # Not a domain name.
        ("soc@example.com.", NOT_AN_ADDRESS),
        ("soc@examplecom", NOT_AN_ADDRESS),
        ("soc@-example.com", NOT_AN_ADDRESS),
        ("soc@exam_ple.com", NOT_AN_ADDRESS),
        ("soc@[192.0.2.10]", NOT_AN_ADDRESS),
        ("soc@192.0.2.10", DOMAIN_NOT_ALLOWED),
        # Not ASCII, not a dot-atom, too long, or with spaces around it.
        ("söc@example.com", NOT_AN_ADDRESS),
        ("soc@exämple.com", NOT_AN_ADDRESS),
        (".soc@example.com", NOT_AN_ADDRESS),
        ("soc.@example.com", NOT_AN_ADDRESS),
        ("s..oc@example.com", NOT_AN_ADDRESS),
        ("a" * 65 + "@example.com", NOT_AN_ADDRESS),
        ("a@" + ".".join(letter * 60 for letter in "bcdef") + ".com", NOT_AN_ADDRESS),
        (" soc@example.com", NOT_AN_ADDRESS),
        ("soc@example.com ", NOT_AN_ADDRESS),
        ("soc@example.com" + chr(0), NOT_AN_ADDRESS),
        ("soc" + chr(0x200B) + "@example.com", NOT_AN_ADDRESS),
        ("", NOT_AN_ADDRESS),
        ("soc", NOT_AN_ADDRESS),
    ],
)
def test_anything_else_is_refused(address: str, reason: RefusalReason) -> None:
    assert refused_recipients([address], ALLOWED) == [RefusedRecipient(address, reason)]


def test_every_refused_recipient_is_reported_in_order() -> None:
    recipients = ["soc-1@example.com", "x@example.net", "soc-2@example.com", "bad"]

    assert refused_recipients(recipients, ALLOWED) == [
        RefusedRecipient("x@example.net", DOMAIN_NOT_ALLOWED),
        RefusedRecipient("bad", NOT_AN_ADDRESS),
    ]


def test_allowed_domains_are_compared_without_case_or_surrounding_spaces() -> None:
    assert refused_recipients(["Soc@Example.COM"], [" EXAMPLE.com "]) == []
    assert normalize_domain(" Bank.Example.ORG ") == "bank.example.org"
    assert address_domain("Soc@Example.COM") == "example.com"


@pytest.mark.parametrize(
    "allowed", ["*.example.com", "@example.com", "example.com.", "com", "", "example .com"]
)
def test_an_allowed_domain_that_is_not_a_domain_name_allows_nothing(allowed: str) -> None:
    assert normalize_domain(allowed) is None
    assert refused_recipients(["soc@example.com"], [allowed]) == [
        RefusedRecipient("soc@example.com", DOMAIN_NOT_ALLOWED)
    ]


def test_without_allowed_domains_nobody_gets_an_email() -> None:
    assert refused_recipients(["soc@example.com"], []) == [
        RefusedRecipient("soc@example.com", DOMAIN_NOT_ALLOWED)
    ]
