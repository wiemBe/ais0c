"""Named recipient groups, the routing table and the allowed domains (T-036 criterion 2).

The functions are what the admin API of T-028 calls; T-020 added the same tables without names.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EmailKind, Level
from ais0c_storage.errors import DuplicateError
from ais0c_storage.repositories import (
    add_allowed_email_domain,
    add_notification_recipient,
    delete_allowed_email_domain,
    delete_notification_recipient,
    list_allowed_email_domains,
    list_notification_recipients,
    list_notification_route_groups,
)

pytestmark = pytest.mark.anyio


async def members(session: AsyncSession, list_name: str | None = None) -> list[tuple[str, str]]:
    return [
        (row.list_name, row.email)
        for row in await list_notification_recipients(session, list_name=list_name)
    ]


async def domains(session: AsyncSession) -> list[str]:
    return [row.domain for row in await list_allowed_email_domains(session)]


# --- criterion 2: group members ---------------------------------------------------------------


async def test_members_are_added_listed_and_deleted(session: AsyncSession) -> None:
    await add_notification_recipient(session, list_name="operators", email="soc-2@example.com")
    await add_notification_recipient(session, list_name="operators", email="soc-1@example.com")
    await add_notification_recipient(session, list_name="exec", email="chief@example.com")

    assert await members(session) == [
        ("exec", "chief@example.com"),
        ("operators", "soc-1@example.com"),
        ("operators", "soc-2@example.com"),
    ]
    assert await members(session, list_name="operators") == [
        ("operators", "soc-1@example.com"),
        ("operators", "soc-2@example.com"),
    ]

    assert await delete_notification_recipient(
        session, list_name="operators", email="soc-1@example.com"
    )
    assert await members(session, list_name="operators") == [("operators", "soc-2@example.com")]


async def test_a_member_of_another_group_is_not_deleted(session: AsyncSession) -> None:
    await add_notification_recipient(session, list_name="operators", email="soc-1@example.com")

    removed = await delete_notification_recipient(
        session, list_name="exec", email="soc-1@example.com"
    )

    assert removed is False
    assert await members(session) == [("operators", "soc-1@example.com")]


async def test_a_group_name_is_the_admin_s_own_name(session: AsyncSession) -> None:
    """Any name the admin picks, not a fixed list; the same address may be in several groups."""
    await add_notification_recipient(session, list_name="soc-on-call-2", email="soc-1@example.com")
    await add_notification_recipient(session, list_name="a", email="soc-1@example.com")

    assert await members(session) == [
        ("a", "soc-1@example.com"),
        ("soc-on-call-2", "soc-1@example.com"),
    ]


async def test_a_duplicate_member_raises_without_ending_the_transaction(
    session: AsyncSession,
) -> None:
    await add_notification_recipient(session, list_name="operators", email="soc-1@example.com")

    with pytest.raises(DuplicateError, match="already exists"):
        await add_notification_recipient(session, list_name="operators", email="soc-1@example.com")

    assert await members(session, list_name="operators") == [("operators", "soc-1@example.com")]
    await add_notification_recipient(session, list_name="operators", email="soc-2@example.com")
    assert len(await members(session, list_name="operators")) == 2


async def test_a_malformed_legacy_member_can_still_be_deleted(session: AsyncSession) -> None:
    """0007 checks the name of a new row; an address an admin wants gone must be removable even
    when the stored text is not a plain address."""
    await add_notification_recipient(session, list_name="operators", email="soc-1@example.com")
    await session.execute(
        text(
            "INSERT INTO notification_recipients (list_name, email) VALUES ('operators',"
            " 'Soc 1@example.com')"
        )
    )

    assert await delete_notification_recipient(
        session, list_name="operators", email="Soc 1@example.com"
    )
    assert await members(session) == [("operators", "soc-1@example.com")]


@pytest.mark.parametrize(
    "list_name",
    ["", "Operators", "-ops", "0ops", "op erators", "op_erators", "ops\n", "ğ", "a" * 64],
    ids=[
        "empty",
        "upper",
        "leading-hyphen",
        "leading-digit",
        "space",
        "underscore",
        "newline",
        "turkish",
        "too-long",
    ],
)
async def test_a_group_name_outside_the_pattern_is_refused(
    session: AsyncSession, list_name: str
) -> None:
    with pytest.raises(ValueError, match=r"\[a-z\]\[a-z0-9-\]\{0,62\}"):
        await add_notification_recipient(session, list_name=list_name, email="soc-1@example.com")
    assert await members(session) == []


@pytest.mark.parametrize(
    "email",
    [
        "soc-1@example.com",
        "soc.1+tag@sub.example.com",
        "a@b.c",
        "Soc-1@EXAMPLE.COM",
    ],
    ids=["plain", "dot-atom", "two-labels", "upper-case"],
)
async def test_a_plain_address_is_stored_with_a_lower_case_domain(
    session: AsyncSession, email: str
) -> None:
    await add_notification_recipient(session, list_name="operators", email=email)

    assert await members(session) == [
        ("operators", email.split("@")[0] + "@" + email.split("@")[1].lower())
    ]


@pytest.mark.parametrize(
    "email",
    [
        "soc-1",
        "@example.com",
        "soc-1@",
        "soc 1@example.com",
        "soc-1@example.com, soc-2@example.net",
        "soc-1@example.com\r\nBcc: x@example.net",
        '"soc-1"@example.com',
        "soc-1@.example.com",
        "soc-1@example..com",
        "soc-1@example.com.",
        "soc-1@localhost",
        "",
        f"{'a' * 65}@example.com",
        f"soc-1@{'a' * 250}.com",
    ],
    ids=[
        "no-at",
        "no-local",
        "no-domain",
        "space",
        "second-recipient",
        "line-break",
        "quoted",
        "empty-label",
        "double-dot",
        "trailing-dot",
        "one-label",
        "empty",
        "long-local",
        "long-domain",
    ],
)
async def test_an_address_that_is_not_a_plain_address_is_refused(
    session: AsyncSession, email: str
) -> None:
    with pytest.raises(ValueError, match="plain ASCII local@domain"):
        await add_notification_recipient(session, list_name="operators", email=email)
    assert await members(session) == []


# --- criterion 2: allowed domains ------------------------------------------------------------


async def test_domains_are_added_listed_and_deleted(session: AsyncSession) -> None:
    await add_allowed_email_domain(session, "example.com")
    await add_allowed_email_domain(session, "  Example.NET ")

    assert await domains(session) == ["example.com", "example.net"]

    assert await delete_allowed_email_domain(session, "EXAMPLE.NET") is True
    assert await domains(session) == ["example.com"]
    assert await delete_allowed_email_domain(session, "example.net") is False


async def test_a_duplicate_domain_raises_without_ending_the_transaction(
    session: AsyncSession,
) -> None:
    await add_allowed_email_domain(session, "example.com")

    with pytest.raises(DuplicateError, match="already exists"):
        await add_allowed_email_domain(session, "EXAMPLE.com")

    assert await domains(session) == ["example.com"]
    await add_allowed_email_domain(session, "example.net")
    assert await domains(session) == ["example.com", "example.net"]


@pytest.mark.parametrize(
    "domain",
    [
        "",
        " ",
        "example",
        "-example.com",
        "example-.com",
        "example..com",
        "example.com.",
        "exa mple.com",
        "exämple.com",
        "*.example.com",
        "a" * 254,
    ],
    ids=[
        "empty",
        "spaces-only",
        "one-label",
        "leading-hyphen",
        "trailing-hyphen",
        "double-dot",
        "trailing-dot",
        "inner-space",
        "turkish",
        "wildcard",
        "too-long",
    ],
)
async def test_a_domain_that_is_not_a_domain_name_is_refused(
    session: AsyncSession, domain: str
) -> None:
    with pytest.raises(ValueError, match="plain DNS domain name"):
        await add_allowed_email_domain(session, domain)
    assert await domains(session) == []


# --- criterion 2: the routing table ----------------------------------------------------------


async def test_the_seeded_routes_name_the_first_groups(session: AsyncSession) -> None:
    """0007 seeds case and group alerts at high and critical and hunt reports, which have no
    level of their own."""
    assert await list_notification_route_groups(
        session, kind=EmailKind.CASE_ALERT, level=Level.HIGH
    ) == ["operators"]
    assert await list_notification_route_groups(
        session, kind=EmailKind.GROUP_ALERT, level=Level.CRITICAL
    ) == ["analyst-eng", "exec", "operators"]
    assert await list_notification_route_groups(
        session, kind=EmailKind.HUNT_REPORT, level=None
    ) == ["hunters"]


async def test_a_level_that_is_not_routed_returns_nothing(session: AsyncSession) -> None:
    assert (
        await list_notification_route_groups(session, kind=EmailKind.CASE_ALERT, level=Level.MEDIUM)
        == []
    )


async def test_the_null_level_is_matched_on_its_own(session: AsyncSession) -> None:
    """`level IS NULL` is one value with NULLS NOT DISTINCT (T-43), not every empty level."""
    assert (
        await list_notification_route_groups(session, kind=EmailKind.HUNT_REPORT, level=Level.LOW)
        == []
    )
