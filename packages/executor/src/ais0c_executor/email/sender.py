"""Sending an alert e-mail once (architecture §9, "E-posta bildirimi"; D-22, D-41, D-42).

`EmailSender.send_alert` decides whether an alert goes out, builds it and sends it:

1. The attempts for one case (a group's case for a group alert) take turns: a PostgreSQL
   advisory lock held until the attempt is recorded. Two attempts of one activity, such as a
   retry that starts while a slow first attempt still runs, cannot both send.
2. An e-mail that `notifications` records as sent under the same idempotency key is not sent
   again (`already_sent`).
3. The level rule (`alert_needed`): only high and critical, and after a re-evaluation only a
   level above every level already e-mailed about the same case or group, as its `sent` alerts
   in `notifications` record them (`level`). Otherwise the result is `not_needed` and nothing is
   recorded.
4. The recipients are the members of the groups `notification_routes` routes to the alert's
   kind and level: every address once, in ascending order (D-41). If one of them is outside the
   allowed domains (`allowed_email_domains`), nobody gets the e-mail: it is recorded as
   `rejected` in `notifications` and as `email.reject` in `audit_log`. An alert that routes to
   no group, or to groups without members, is recorded as `failed`. Both checks run before the
   kill switch, so a bad list shows up in shadow mode already.
5. The kill switch is checked before the relay is called and again right before the e-mail
   leaves. With writes off nothing is sent; the attempt is recorded as `disabled`
   (`writes_disabled`), which is not a failure (T-37).
6. If the relay takes the e-mail, it is recorded as `sent` in `notifications` and as
   `email.send` in `audit_log`. If not, it is recorded as `failed`. When a later attempt may
   succeed, the error is raised after the record is committed, so Temporal retries the
   activity.

A health alarm (`HealthAlarm`, T-032) is not an AI output and follows a shorter path: no level
rule, and no kill switch. An intake that stopped has to reach the team exactly when the kill
switch is off after an AI incident, so this one kind is exempt from the check (decision T-68
(6)); notes and the other e-mails keep it. Its recipients come from the route of the kind
`health_alarm` without a level, and its record in `notifications` has no case, group or level.

Every record carries the alert's recipients, subject and level; a `failed` or `rejected` one
also says why in `error`. Only `sent` counts as sent: a key recorded as `disabled`, `rejected`
or `failed` is tried again by a later attempt, and the level rule counts none of them.

A crash after the relay took the e-mail but before the record was committed leaves no record,
so the next attempt sends the e-mail again; the copy has the same Message-ID.
"""

import hashlib
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Final, Protocol

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import EmailKind, EmailMessage, Level
from ais0c_executor.common import EXECUTOR_ID, KillSwitch, WritesDisabled, clean_text
from ais0c_executor.email.addresses import RefusedRecipient, refused_recipients
from ais0c_executor.email.errors import EmailTransportError
from ais0c_executor.email.levels import alert_needed
from ais0c_executor.email.render import alert_message, render_body
from ais0c_executor.email.request import (
    AlertRequest,
    CaseAlert,
    EmailOutcome,
    EmailRequest,
    EmailResult,
    GroupAlert,
    HealthAlarm,
    validated,
)
from ais0c_executor.email.smtp import SendReceipt
from ais0c_storage import ActorKind, NotificationStatus
from ais0c_storage.models import AllowedEmailDomainRow, NotificationRecipientRow, NotificationRow
from ais0c_storage.repositories import (
    append_audit,
    get_notification,
    list_notification_route_groups,
    list_notifications,
    record_notification,
)

EMAIL_SEND_ACTION: Final = "email.send"
EMAIL_REJECT_ACTION: Final = "email.reject"
# The audit entry's object: the case of a case alert, the group of a group alert.
CASE_OBJECT_TYPE: Final = "case"
GROUP_OBJECT_TYPE: Final = "offense_group"
HEALTH_ALARM_OBJECT_TYPE: Final = "health_alarm"
MAX_ERROR_LENGTH: Final = 500
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
        turn = (
            f"health_alarm:{request.alarm_id}"
            if isinstance(request, HealthAlarm)
            else request.case_id
        )
        async with self._sessions.begin() as session:
            await _take_turn(session, turn)
            if isinstance(request, HealthAlarm):
                outcome, failure = await self._attempt_health_alarm(session, request)
            else:
                outcome, failure = await self._attempt(session, request)
        if failure is not None and failure.retryable:
            raise failure
        return outcome

    async def _attempt_health_alarm(
        self, session: AsyncSession, request: HealthAlarm
    ) -> tuple[EmailOutcome, EmailTransportError | None]:
        """A health alarm: the recipients' checks of an alert, then the relay, without the kill
        switch (T-68 (6))."""
        row = await get_notification(session, request.idempotency_key)
        if row is not None and row.status is NotificationStatus.SENT:
            return _outcome(request, EmailResult.ALREADY_SENT), None
        groups, recipients = await _recipients(session, request)
        message = alert_message(request, recipients)
        if not recipients:
            error = _no_recipients_error(request, groups)
            outcome = _outcome(request, EmailResult.FAILED, error)
            await _save(session, request, message, NotificationStatus.FAILED, error=outcome.error)
            return outcome, None
        refused = refused_recipients(recipients, await _allowed_domains(session))
        if refused:
            error = (
                f"{len(refused)} of {len(recipients)} recipients are not plain addresses in the "
                "allowed domains; the e-mail went to nobody"
            )
            outcome = _outcome(request, EmailResult.REJECTED, error)
            await _save(session, request, message, NotificationStatus.REJECTED, error=outcome.error)
            await _audit(session, request, message, EMAIL_REJECT_ACTION, refused=refused)
            return outcome, None
        body = render_body(message)
        try:
            async with self._transport.connect() as connection:
                receipt = await connection.send(message, body)
        except EmailTransportError as error:
            outcome = _outcome(request, EmailResult.FAILED, str(error))
            await _save(session, request, message, NotificationStatus.FAILED, error=outcome.error)
            return outcome, error
        await _save(session, request, message, NotificationStatus.SENT, sent_at=self._clock())
        await _audit(session, request, message, EMAIL_SEND_ACTION, receipt=receipt)
        return _outcome(request, EmailResult.SENT), None

    async def _attempt(
        self, session: AsyncSession, request: AlertRequest
    ) -> tuple[EmailOutcome, EmailTransportError | None]:
        row = await get_notification(session, request.idempotency_key)
        if row is not None and row.status is NotificationStatus.SENT:
            return _outcome(request, EmailResult.ALREADY_SENT), None
        if not alert_needed(request.level, await _sent_levels(session, request)):
            return _outcome(request, EmailResult.NOT_NEEDED), None

        groups, recipients = await _recipients(session, request)
        message = alert_message(request, recipients)
        if not recipients:
            error = _no_recipients_error(request, groups)
            outcome = _outcome(request, EmailResult.FAILED, error)
            await _save(session, request, message, NotificationStatus.FAILED, error=outcome.error)
            return outcome, None
        refused = refused_recipients(recipients, await _allowed_domains(session))
        if refused:
            error = (
                f"{len(refused)} of {len(recipients)} recipients are not plain addresses in the "
                "allowed domains; the e-mail went to nobody"
            )
            outcome = _outcome(request, EmailResult.REJECTED, error)
            await _save(session, request, message, NotificationStatus.REJECTED, error=outcome.error)
            await _audit(session, request, message, EMAIL_REJECT_ACTION, refused=refused)
            return outcome, None

        body = render_body(message)
        try:
            await self._kill_switch.check()
            async with self._transport.connect() as connection:
                receipt = await self._kill_switch.guarded(lambda: connection.send(message, body))
        except WritesDisabled as error:
            await _save(session, request, message, NotificationStatus.DISABLED)
            return _outcome(request, EmailResult.WRITES_DISABLED, str(error)), None
        except EmailTransportError as error:
            outcome = _outcome(request, EmailResult.FAILED, str(error))
            await _save(session, request, message, NotificationStatus.FAILED, error=outcome.error)
            return outcome, error
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


async def _sent_levels(session: AsyncSession, request: AlertRequest) -> list[Level]:
    """The levels of the alerts sent about the same case or group (D-42).

    A case alert counts the `sent` case alerts of its case; a group alert the `sent` group alerts
    of its group. The `group_alert:<group>` records T-020 wrote before the key carried an
    evaluation number carry the same `group_id`, so the old keys need no rule of their own.
    """
    if isinstance(request, CaseAlert):
        kind = EmailKind.CASE_ALERT
        rows = await list_notifications(session, case_id=request.case_id)
    else:
        kind = EmailKind.GROUP_ALERT
        rows = await list_notifications(session, group_id=request.group_id)
    return [
        row.level
        for row in rows
        if row.kind is kind
        and row.status is NotificationStatus.SENT
        # Empty only on a row written before the column existed (migration 0004). Skipping it
        # can send one e-mail too many, never one too few.
        and row.level is not None
    ]


async def _recipients(session: AsyncSession, request: EmailRequest) -> tuple[list[str], list[str]]:
    """The groups routed to the alert's kind and level, and their members.

    Every address of every routed group, each one once: two routed groups may share a member,
    and the address is written to the envelope a single time.
    """
    level = None if isinstance(request, HealthAlarm) else request.level
    groups = await list_notification_route_groups(session, kind=request.email_kind, level=level)
    if not groups:
        return [], []
    statement = (
        select(NotificationRecipientRow.email)
        .where(NotificationRecipientRow.list_name.in_(groups))
        .distinct()
        .order_by(NotificationRecipientRow.email)
    )
    return groups, list(await session.scalars(statement))


def _no_recipients_error(request: EmailRequest, groups: Sequence[str]) -> str:
    """Why nobody was found for `request`: either no group is routed to it, or the routed
    groups hold no member. Both are the admin's routing table and groups to fix."""
    if isinstance(request, HealthAlarm):
        routed = "a health alarm"
    else:
        routed = f"a {request.level.value} {request.email_kind.value}"
    if not groups:
        return f"no recipient group is routed to {routed}"
    return f"the recipient groups routed to {routed} ({', '.join(groups)}) have no members"


async def _allowed_domains(session: AsyncSession) -> list[str]:
    return list(await session.scalars(select(AllowedEmailDomainRow.domain)))


async def _save(
    session: AsyncSession,
    request: EmailRequest,
    message: EmailMessage,
    status: NotificationStatus,
    *,
    sent_at: datetime | None = None,
    error: str | None = None,
) -> None:
    """Insert the e-mail's `notifications` row, or update the one an earlier attempt made.

    An update rewrites the recipients, the subject and the error too: the list may have changed
    since, and only the latest attempt's reason holds.
    """
    level = None if isinstance(request, HealthAlarm) else request.level
    if await get_notification(session, message.idempotency_key) is None:
        group_id = request.group_id if isinstance(request, GroupAlert) else None
        await record_notification(
            session,
            message,
            status=status,
            level=level,
            case_id=None if isinstance(request, HealthAlarm) else request.case_id,
            group_id=group_id,
            sent_at=sent_at,
            error=error,
        )
        return
    statement = (
        update(NotificationRow)
        .where(NotificationRow.idempotency_key == message.idempotency_key)
        .values(
            level=level,
            recipients=list(message.recipients),
            subject=message.subject,
            status=status,
            error=error,
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
    if isinstance(request, HealthAlarm):
        await append_audit(
            session,
            actor_kind=ActorKind.SYSTEM,
            actor_id=EXECUTOR_ID,
            action=action,
            object_type=HEALTH_ALARM_OBJECT_TYPE,
            object_id=request.alarm_id,
            details=_alarm_details(request, message, receipt=receipt, refused=refused),
        )
        return
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


def _alarm_details(
    request: HealthAlarm,
    message: EmailMessage,
    *,
    receipt: SendReceipt | None,
    refused: list[RefusedRecipient] | None,
) -> dict[str, JsonValue]:
    details: dict[str, JsonValue] = {
        "kind": message.kind.value,
        "idempotency_key": message.idempotency_key,
        "alarm_kind": request.alarm_kind,
        "subject": clean_text(request.subject, 200),
        "status": request.status,
        "subject_line": message.subject,
        "recipients": list(message.recipients),
    }
    if receipt is not None:
        details["message_id"] = receipt.message_id
        details["refused_by_relay"] = list(receipt.refused)
    if refused:
        details["refused"] = [
            {"address": item.address[:_MAX_AUDIT_ADDRESS_LENGTH], "reason": item.reason.value}
            for item in refused
        ]
    return details
