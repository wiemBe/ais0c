"""`notifications`: e-mails the executor sent, refused or held back (architecture §9, D-22).

`idempotency_key` is unique, so the same e-mail cannot be recorded twice. `level` is an alert's
notify level: a re-evaluated case is e-mailed again only above the levels already sent. Only
`sent` counts as sent; `error` says why a `failed` or `rejected` e-mail was not (T-37).
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EmailKind, EmailMessage, Level
from ais0c_storage.columns import revalidate
from ais0c_storage.enums import NotificationStatus
from ais0c_storage.ids import uuid7_floor
from ais0c_storage.models import NotificationRow
from ais0c_storage.repositories._common import fetch_all, fetch_one, insert_new, update_one

# The statuses whose record says why the e-mail was not sent (T-37).
_STATUSES_WITH_ERROR = frozenset({NotificationStatus.FAILED, NotificationStatus.REJECTED})


# The kinds without a notify level.
_LEVELLESS_KINDS = frozenset({EmailKind.HUNT_REPORT, EmailKind.HEALTH_ALARM})


def _check_sent_at(status: NotificationStatus, sent_at: datetime | None) -> None:
    if (status is NotificationStatus.SENT) != (sent_at is not None):
        raise ValueError("sent_at is required for a sent e-mail and only for it")


def _check_error(status: NotificationStatus, error: str | None) -> None:
    if error is not None and NotificationStatus(status) not in _STATUSES_WITH_ERROR:
        raise ValueError("only a failed or rejected e-mail has an error")


async def record_notification(
    session: AsyncSession,
    message: EmailMessage,
    *,
    status: NotificationStatus,
    level: Level | None = None,
    case_id: str | None = None,
    hunt_id: str | None = None,
    group_id: str | None = None,
    sent_at: datetime | None = None,
    error: str | None = None,
) -> NotificationRow:
    """Record the outcome of sending `message`.

    The ID matching the kind is required: `case_id` for a case alert, `group_id` for a group
    alert, `hunt_id` for a hunt report. So is `level` for a case or group alert; a hunt report
    has none. `error` is only for a `failed` or `rejected` e-mail. Raises `DuplicateError` if
    the idempotency key is recorded.
    """
    message = revalidate(EmailMessage, message)
    subject_ids = {
        EmailKind.CASE_ALERT: case_id,
        EmailKind.GROUP_ALERT: group_id,
        EmailKind.HUNT_REPORT: hunt_id,
    }
    # A health alarm is about the platform, not a case, a group or a hunt (T-032).
    if message.kind is not EmailKind.HEALTH_ALARM and subject_ids[message.kind] is None:
        raise ValueError(f"a {message.kind.value} needs the ID of what it reports on")
    if (level is None) != (message.kind in _LEVELLESS_KINDS):
        raise ValueError("a case or group alert needs its notify level, and only an alert has one")
    _check_sent_at(status, sent_at)
    _check_error(status, error)
    values = dict(
        kind=message.kind,
        level=level,
        case_id=case_id,
        hunt_id=hunt_id,
        group_id=group_id,
        recipients=message.recipients,
        subject=message.subject,
        idempotency_key=message.idempotency_key,
        status=status,
        error=error,
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
    error: str | None = None,
) -> NotificationRow:
    """For example `disabled` or `failed` -> `sent` when a later attempt succeeds. `error`
    replaces the old one."""
    _check_sent_at(status, sent_at)
    _check_error(status, error)
    statement = (
        update(NotificationRow)
        .where(NotificationRow.idempotency_key == idempotency_key)
        .values(status=status, sent_at=sent_at, error=error)
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


async def count_failed_notifications(session: AsyncSession, *, since: datetime) -> int:
    """How many e-mails were first recorded at or after `since` and are `failed` or `rejected`
    now (T-032 criterion 4). `disabled` is never counted (T-37).

    `notifications` has no creation time; the ID is a UUIDv7, which carries it. A row that
    failed again on a later attempt counts from its first record.
    """
    statement = (
        select(func.count())
        .select_from(NotificationRow)
        .where(
            NotificationRow.status.in_(_STATUSES_WITH_ERROR),
            NotificationRow.id >= uuid7_floor(since),
        )
    )
    return int(await session.scalar(statement) or 0)
