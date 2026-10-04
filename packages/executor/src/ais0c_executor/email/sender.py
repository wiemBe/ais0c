"""Sending an alert e-mail once (architecture §9, "E-posta bildirimi"; D-22, T-23).

`EmailSender.send_alert` decides whether an alert goes out, builds it and sends it:

1. The attempts for one case (a group's case for a group alert) take turns: a PostgreSQL
   advisory lock held until the attempt is recorded. Two attempts of one activity, such as a
   retry that starts while a slow first attempt still runs, cannot both send.
2. An e-mail that `notifications` records as sent under the same idempotency key is not sent
   again (`already_sent`).
3. The level rule (`alert_needed`): only high and critical, and after a re-evaluation only a
   level above every level already e-mailed about the case. Those levels come from the case's
   `email.send` entries in `audit_log`, because `notifications` has no level column.
   Otherwise the result is `not_needed` and nothing is recorded.
4. The recipients are the `operators` list (`notification_recipients`). If one of them is
   outside the allowed domains (`allowed_email_domains`), nobody gets the e-mail: it is
   recorded as `rejected` in `notifications` and as `email.reject` in `audit_log`. An empty
   list is recorded as `failed`.
5. The kill switch is checked before the relay is called and again right before the e-mail
   leaves. With writes off nothing is sent or recorded (`writes_disabled`).
6. If the relay takes the e-mail, it is recorded as `sent` in `notifications` and as
   `email.send` in `audit_log`, with its level. If not, it is recorded as `failed`. When a
   later attempt may succeed, the error is raised after the record is committed, so Temporal
   retries the activity.

A crash after the relay took the e-mail but before the record was committed leaves no record,
so the next attempt sends the e-mail again; the copy has the same Message-ID.
"""

import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Final, Protocol

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import EmailMessage, Level
from ais0c_executor.common import KillSwitch, WritesDisabled, clean_text
from ais0c_executor.email.addresses import RefusedRecipient, refused_recipients
from ais0c_executor.email.errors import EmailTransportError
from ais0c_executor.email.levels import alert_needed
from ais0c_executor.email.render import alert_message, render_body
from ais0c_executor.email.request import (
    CaseAlert,
    EmailOutcome,
    EmailRequest,
    EmailResult,
    GroupAlert,
    validated,
)
from ais0c_executor.email.smtp import SendReceipt
from ais0c_storage import ActorKind, NotificationStatus, RecipientList
from ais0c_storage.models import AllowedEmailDomainRow, NotificationRecipientRow, NotificationRow
from ais0c_storage.repositories import (
    append_audit,
    get_notification,
    list_audit,
    record_notification,
)

# Who sends e-mails, in audit_log.
EXECUTOR_ID: Final = "action-executor"
EMAIL_SEND_ACTION: Final = "email.send"
EMAIL_REJECT_ACTION: Final = "email.reject"
# The audit entry's object: the case of a case alert, the group of a group alert.
CASE_OBJECT_TYPE: Final = "case"
GROUP_OBJECT_TYPE: Final = "offense_group"
# Who gets alerts (architecture §9: "SOC operatörleri").
ALERT_RECIPIENTS: Final = RecipientList.OPERATORS
MAX_ERROR_LENGTH: Final = 500
# How many of the case's e-mail entries are read for the level rule; a case has a few at most.
_HISTORY_LIMIT: Final = 1000
_MAX_AUDIT_ADDRESS_LENGTH: Final = 320


class EmailConnection(Protocol):
    """An open connection to the relay."""

    async def send(self, message: EmailMessage, body: str) -> SendReceipt:
        """Raises `EmailTransportError` when the relay does not take the e-mail."""
        ...


class EmailTransport(Protocol):
    """The relay (`SmtpTransport`)."""

    def connect(self) -> AbstractAsyncContextManager[EmailConnection]:
        """Raises `EmailTransportError` when the connection cannot be opened."""
        ...


def utc_now() -> datetime:
    return datetime.now(UTC)


class EmailSender:
    """Sends alert e-mails through `transport` and records them through `sessions`."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        transport: EmailTransport,
        kill_switch: KillSwitch | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._transport = transport
        self._kill_switch = KillSwitch(sessions) if kill_switch is None else kill_switch
        self._clock = clock

    async def send_alert(self, request: EmailRequest) -> EmailOutcome:
        """Send the alert of `request` unless it was sent already, is not needed, is refused or
        writes are off.

        Raises `InvalidEmail` when the e-mail cannot be built; nothing is recorded. Raises
        `EmailTransportError` when the relay did not take the e-mail and a later attempt may
        succeed; the attempt is recorded as `failed` first. A relay that refused the e-mail for
        good is recorded and returned as `failed`.
        """
        request = validated(request)
        async with self._sessions.begin() as session:
            await _take_turn(session, request.case_id)
            outcome, failure = await self._attempt(session, request)
        if failure is not None and failure.retryable:
            raise failure
        return outcome

    async def _attempt(
        self, session: AsyncSession, request: EmailRequest
    ) -> tuple[EmailOutcome, EmailTransportError | None]:
        row = await get_notification(session, request.idempotency_key)
        if row is not None and row.status is NotificationStatus.SENT:
            return _outcome(request, EmailResult.ALREADY_SENT), None
        if not alert_needed(request.level, await _sent_levels(session, request)):
            return _outcome(request, EmailResult.NOT_NEEDED), None

        recipients = await _recipients(session, ALERT_RECIPIENTS)
        message = alert_message(request, recipients)
        if not recipients:
            await _save(session, request, message, NotificationStatus.FAILED)
            error = f"the {ALERT_RECIPIENTS.value} recipient list is empty"
            return _outcome(request, EmailResult.FAILED, error), None
        refused = refused_recipients(recipients, await _allowed_domains(session))
        if refused:
            await _save(session, request, message, NotificationStatus.REJECTED)
            await _audit(session, request, message, EMAIL_REJECT_ACTION, refused=refused)
            error = (
                f"{len(refused)} of {len(recipients)} recipients are not plain addresses in the "
                "allowed domains; the e-mail went to nobody"
            )
            return _outcome(request, EmailResult.REJECTED, error), None

        body = render_body(message)
        try:
            await self._kill_switch.check()
            async with self._transport.connect() as connection:
                receipt = await self._kill_switch.guarded(lambda: connection.send(message, body))
        except WritesDisabled as error:
            return _outcome(request, EmailResult.WRITES_DISABLED, str(error)), None
        except EmailTransportError as error:
            await _save(session, request, message, NotificationStatus.FAILED)
            return _outcome(request, EmailResult.FAILED, str(error)), error
        await _save(session, request, message, NotificationStatus.SENT, sent_at=self._clock())
        await _audit(session, request, message, EMAIL_SEND_ACTION, receipt=receipt)
        return _outcome(request, EmailResult.SENT), None


def _outcome(request: EmailRequest, result: EmailResult, error: str | None = None) -> EmailOutcome:
    return EmailOutcome(
        result=result,
        kind=request.email_kind,
        idempotency_key=request.idempotency_key,
        error=None if error is None else clean_text(error, MAX_ERROR_LENGTH),
    )


async def _take_turn(session: AsyncSession, case_id: str) -> None:
    """Wait for the case's advisory lock; it is released when the transaction ends."""
    digest = hashlib.sha256(f"ais0c.email:{case_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def _sent_levels(session: AsyncSession, request: EmailRequest) -> list[Level]:
    """The levels of the case alerts sent about the case. A group alert has its own key: one
    per group."""
    if not isinstance(request, CaseAlert):
        return []
    entries = await list_audit(
        session,
        object_type=CASE_OBJECT_TYPE,
        object_id=request.case_id,
        action=EMAIL_SEND_ACTION,
        limit=_HISTORY_LIMIT,
    )
    levels: list[Level] = []
    for entry in entries:
        level = entry.details.get("level")
        if entry.details.get("kind") != request.kind or not isinstance(level, str):
            continue
        try:
            levels.append(Level(level))
        except ValueError:
            # Not an entry this module wrote. Skipping it can send one e-mail too many, never
            # one too few.
            continue
    return levels


async def _recipients(session: AsyncSession, list_name: RecipientList) -> list[str]:
    statement = (
        select(NotificationRecipientRow.email)
        .where(NotificationRecipientRow.list_name == list_name)
        .order_by(NotificationRecipientRow.email)
    )
    return list(await session.scalars(statement))


async def _allowed_domains(session: AsyncSession) -> list[str]:
    return list(await session.scalars(select(AllowedEmailDomainRow.domain)))


async def _save(
    session: AsyncSession,
    request: EmailRequest,
    message: EmailMessage,
    status: NotificationStatus,
    *,
    sent_at: datetime | None = None,
) -> None:
    """Insert the e-mail's `notifications` row, or update the one an earlier attempt made.

    An update rewrites the recipients and the subject too: the list may have changed since.
    """
    if await get_notification(session, message.idempotency_key) is None:
        group_id = request.group_id if isinstance(request, GroupAlert) else None
        await record_notification(
            session,
            message,
            status=status,
            case_id=request.case_id,
            group_id=group_id,
            sent_at=sent_at,
        )
        return
    statement = (
        update(NotificationRow)
        .where(NotificationRow.idempotency_key == message.idempotency_key)
        .values(
            recipients=list(message.recipients),
            subject=message.subject,
            status=status,
            sent_at=sent_at,
        )
    )
    await session.execute(statement)


async def _audit(
    session: AsyncSession,
    request: EmailRequest,
    message: EmailMessage,
    action: str,
    *,
    receipt: SendReceipt | None = None,
    refused: list[RefusedRecipient] | None = None,
) -> None:
    details: dict[str, JsonValue] = {
        "kind": message.kind.value,
        "idempotency_key": message.idempotency_key,
        "case_id": request.case_id,
        "evaluation_no": request.evaluation_no,
        "level": request.level.value,
        "subject": message.subject,
        "recipients": list(message.recipients),
    }
    if isinstance(request, GroupAlert):
        object_type, object_id = GROUP_OBJECT_TYPE, request.group_id
        details["group_id"] = request.group_id
    else:
        object_type, object_id = CASE_OBJECT_TYPE, request.case_id
    if receipt is not None:
        details["message_id"] = receipt.message_id
        details["refused_by_relay"] = list(receipt.refused)
    if refused:
        details["refused"] = [
            {"address": item.address[:_MAX_AUDIT_ADDRESS_LENGTH], "reason": item.reason.value}
            for item in refused
        ]
    await append_audit(
        session,
        actor_kind=ActorKind.SYSTEM,
        actor_id=EXECUTOR_ID,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=details,
    )
