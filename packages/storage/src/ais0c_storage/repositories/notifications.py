"""`notifications`: e-mails the executor sent or refused (architecture §9, D-22).

`idempotency_key` is unique, so the same e-mail cannot be recorded twice.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EmailKind, EmailMessage
from ais0c_storage.columns import revalidate
from ais0c_storage.enums import NotificationStatus
from ais0c_storage.models import NotificationRow
from ais0c_storage.repositories._common import fetch_all, fetch_one, insert_new, update_one


def _check_sent_at(status: NotificationStatus, sent_at: datetime | None) -> None:
    if (status is NotificationStatus.SENT) != (sent_at is not None):
        raise ValueError("sent_at is required for a sent e-mail and only for it")


async def record_notification(
    session: AsyncSession,
    message: EmailMessage,
    *,
    status: NotificationStatus,
    case_id: str | None = None,
    hunt_id: str | None = None,
    group_id: str | None = None,
    sent_at: datetime | None = None,
) -> NotificationRow:
    """Record the outcome of sending `message`.

    The ID matching the kind is required: `case_id` for a case alert, `group_id` for a group
    alert, `hunt_id` for a hunt report. Raises `DuplicateError` if the idempotency key is
    recorded.
    """
    message = revalidate(EmailMessage, message)
    subject_ids = {
        EmailKind.CASE_ALERT: case_id,
        EmailKind.GROUP_ALERT: group_id,
        EmailKind.HUNT_REPORT: hunt_id,
    }
    if subject_ids[message.kind] is None:
        raise ValueError(f"a {message.kind.value} needs the ID of what it reports on")
    _check_sent_at(status, sent_at)
    values = dict(
        kind=message.kind,
        case_id=case_id,
        hunt_id=hunt_id,
        group_id=group_id,
        recipients=message.recipients,
        subject=message.subject,
        idempotency_key=message.idempotency_key,
        status=status,
        sent_at=sent_at,
    )
    return await insert_new(session, NotificationRow, values, f"e-mail {message.idempotency_key!r}")


async def get_notification(session: AsyncSession, idempotency_key: str) -> NotificationRow | None:
    statement = select(NotificationRow).where(NotificationRow.idempotency_key == idempotency_key)
    return await fetch_one(session, statement)


async def update_notification_status(
    session: AsyncSession,
    idempotency_key: str,
    *,
    status: NotificationStatus,
    sent_at: datetime | None = None,
) -> NotificationRow:
    """For example `failed` -> `sent` when a retry succeeds."""
    _check_sent_at(status, sent_at)
    statement = (
        update(NotificationRow)
        .where(NotificationRow.idempotency_key == idempotency_key)
        .values(status=status, sent_at=sent_at)
    )
    return await update_one(session, statement, NotificationRow, f"e-mail {idempotency_key!r}")


async def list_notifications(
    session: AsyncSession,
    *,
    case_id: str | None = None,
    hunt_id: str | None = None,
    group_id: str | None = None,
) -> list[NotificationRow]:
    """E-mails about a case, a hunt or a group."""
    if case_id is None and hunt_id is None and group_id is None:
        raise ValueError("case_id, hunt_id or group_id is required")
    statement = select(NotificationRow)
    if case_id is not None:
        statement = statement.where(NotificationRow.case_id == case_id)
    if hunt_id is not None:
        statement = statement.where(NotificationRow.hunt_id == hunt_id)
    if group_id is not None:
        statement = statement.where(NotificationRow.group_id == group_id)
    statement = statement.order_by(NotificationRow.sent_at.nulls_last(), NotificationRow.id)
    return await fetch_all(session, statement)
