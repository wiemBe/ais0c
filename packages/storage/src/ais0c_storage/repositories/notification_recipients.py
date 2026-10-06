"""Named notification recipient groups and allowed domains for the admin API (D-41, T-43).

Adds check basic address and domain syntax. Delivery policy stays in the executor, which
checks every recipient against the current domain allowlist immediately before sending.
"""

import re
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.models import AllowedEmailDomainRow, NotificationRecipientRow
from ais0c_storage.repositories._common import fetch_all, insert_new, insert_row

_GROUP_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DOMAIN = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
_ADDRESS = re.compile(rf"(?P<local>{_ATOM}(?:\.{_ATOM})*)@(?P<domain>{_DOMAIN.pattern})")


def _check_group_name(list_name: str) -> None:
    if _GROUP_NAME.fullmatch(list_name) is None:
        raise ValueError("a recipient group name must match [a-z][a-z0-9-]{0,62}")


def _normalized_address(email: str) -> str:
    match = _ADDRESS.fullmatch(email)
    if len(email) > 254 or match is None or len(match["local"]) > 64:
        raise ValueError("a recipient must be a plain ASCII local@domain address")
    return f"{match['local']}@{match['domain'].lower()}"


def _normalized_domain(domain: str) -> str:
    value = domain.strip().lower()
    if len(value) > 253 or _DOMAIN.fullmatch(value) is None:
        raise ValueError("an allowed e-mail domain must be a plain DNS domain name")
    return value


async def add_notification_recipient(
    session: AsyncSession, *, list_name: str, email: str
) -> NotificationRecipientRow:
    """Add a group member, preserving the local part and lowercasing its domain.

    A duplicate member raises `DuplicateError` without ending the caller's transaction.
    """
    _check_group_name(list_name)
    values = dict(list_name=list_name, email=_normalized_address(email))
    return await insert_new(session, NotificationRecipientRow, values, "recipient group member")


async def delete_notification_recipient(
    session: AsyncSession, *, list_name: str, email: str
) -> bool:
    """Remove the exact stored address; False when it was not in this group.

    Deletion permits malformed legacy addresses so an admin can remove them.
    """
    statement = (
        delete(NotificationRecipientRow)
        .where(
            NotificationRecipientRow.list_name == list_name,
            NotificationRecipientRow.email == email,
        )
        .returning(NotificationRecipientRow.email)
    )
    return await session.scalar(statement) is not None


async def list_notification_recipients(
    session: AsyncSession, *, list_name: str | None = None
) -> list[NotificationRecipientRow]:
    """List members of one group, or all groups, ordered by group name and address."""
    statement = select(NotificationRecipientRow)
    if list_name is not None:
        statement = statement.where(NotificationRecipientRow.list_name == list_name)
    statement = statement.order_by(
        NotificationRecipientRow.list_name, NotificationRecipientRow.email
    )
    return await fetch_all(session, statement)


async def add_allowed_email_domain(session: AsyncSession, domain: str) -> AllowedEmailDomainRow:
    """Add a domain in lowercase without surrounding spaces; reject invalid DNS syntax.

    A duplicate domain raises `DuplicateError` without ending the caller's transaction.
    """
    values = dict(domain=_normalized_domain(domain))
    return await insert_new(session, AllowedEmailDomainRow, values, "allowed e-mail domain")


async def delete_allowed_email_domain(session: AsyncSession, domain: str) -> bool:
    """Remove a domain, compared in its normalized form; False when it was absent."""
    statement = (
        delete(AllowedEmailDomainRow)
        .where(AllowedEmailDomainRow.domain == domain.strip().lower())
        .returning(AllowedEmailDomainRow.domain)
    )
    return await session.scalar(statement) is not None


async def list_allowed_email_domains(session: AsyncSession) -> list[AllowedEmailDomainRow]:
    """Return the domain allowlist in ascending order."""
    return await fetch_all(
        session, select(AllowedEmailDomainRow).order_by(AllowedEmailDomainRow.domain)
    )


async def check_recipient_domains(session: AsyncSession, domains: Iterable[str]) -> list[str]:
    """The given domains the allowlist does not contain, in the order they were given.

    Comparison is on the normalized form, so `Example.COM` is checked as `example.com`.
    `domains` holds plain DNS names; a name that is not one is returned as not allowed rather
    than raising, so a caller that validates separately and a caller that does not agree.
    """
    wanted: list[str] = []
    for domain in domains:
        try:
            normalized = _normalized_domain(domain)
        except ValueError:
            wanted.append(domain)
        else:
            wanted.append(normalized)
    if not wanted:
        return []
    statement = select(AllowedEmailDomainRow.domain).where(AllowedEmailDomainRow.domain.in_(wanted))
    allowed = set(await session.scalars(statement))
    return list(dict.fromkeys(domain for domain in wanted if domain not in allowed))


async def replace_notification_recipients(
    session: AsyncSession, list_name: str, emails: Iterable[str]
) -> list[NotificationRecipientRow]:
    """Make the group `list_name` hold exactly `emails` (`PUT /notification-recipients/{name}`).

    A new `list_name` creates the group. Addresses are stored as `add_notification_recipient`
    stores them, so the local part is kept and the domain is lowercased; a repeated address is
    written once. Every address must already have passed the domain allowlist; this function
    checks syntax only, and the domain check is the caller's (`check_recipient_domains`).

    Raises ValueError for a name or an address outside the accepted forms. The replacement is
    one transaction, so a group is never left half replaced.
    """
    _check_group_name(list_name)
    addresses = [_normalized_address(email) for email in emails]
    await session.execute(
        delete(NotificationRecipientRow).where(NotificationRecipientRow.list_name == list_name)
    )
    return [
        await insert_row(
            session,
            NotificationRecipientRow,
            dict(list_name=list_name, email=address),
        )
        for address in dict.fromkeys(addresses)
    ]
