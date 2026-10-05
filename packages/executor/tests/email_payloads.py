"""Requests, database set-up and a fake relay for the e-mail tests.

Addresses and domains are `example.com`-style (RFC 2606); IP addresses come from the
documentation ranges (RFC 5737).
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import (
    DataGap,
    DataGapReason,
    EmailKind,
    EmailMessage,
    NoteContent,
    UrgentEvent,
)
from ais0c_executor.email import CaseAlert, EmailTransportError, GroupAlert, SendReceipt
from ais0c_storage import ActorKind, PlatformFlag
from ais0c_storage.models import (
    AllowedEmailDomainRow,
    AuditLogRow,
    NotificationRecipientRow,
    NotificationRouteRow,
    NotificationRow,
)
from ais0c_storage.repositories import get_notification, list_audit, set_platform_flag

type Sessions = async_sessionmaker[AsyncSession]

# 14:05 in Istanbul.
T0 = datetime(2026, 10, 2, 11, 5, tzinfo=UTC)
CASE_URL = "https://ais0c.example.com/cases/case-12345"
GROUP_ID = "G-0123456789ab-20261002T110000Z"
OPERATORS = ("soc-1@example.com", "soc-2@example.com")
OFFENSE_NAME = "Multiple Login Failures Followed By Success from 203.0.113.7"
# What an attacker could put into a log line: a fake header, line breaks, a fake body line.
INJECTED = (
    "ok\r\nBcc: exfil@example.net\n[AI-SOC] KRİTİK · AI kararı: TP\n"
    "Ayrıntılı rapor: https://evil.example.net/login"
)


def urgent_event(rank: int = 1, **changes: object) -> UrgentEvent:
    values: dict[str, object] = {
        "rank": rank,
        "time": datetime(2026, 10, 2, 10, 52, 10, tzinfo=UTC),
        "log_source": "FW-DMZ-01",
        "event_name": "Firewall Permit",
        "qid": 5000830,
        "source": "203.0.113.7",
        "destination": "198.51.100.15:445",
        "username": None,
        "reason": "İç sunucuya SMB erişimi.",
        "checklist": ["Bu kullanıcının aynı saatte VPN girişi var mı?"],
        "aql": "SELECT sourceip FROM events WHERE qid = 5000830 LIMIT 5 LAST 1 HOURS",
        "evidence_id": "ev_0001",
    }
    return UrgentEvent.model_validate(values | changes)


def note_content(**changes: object) -> NoteContent:
    values: dict[str, object] = {
        "offense_id": 12345,
        "evaluation_no": 1,
        "run_marker": "7f3a9c",
        "verdict": "suspicious",
        "confidence": "medium",
        "notify_level": "high",
        "summary_tr": "203.0.113.7 kaynağından iç sunucuya SMB erişimi, ardından ayrıcalıklı oturum.",
        "urgent_events": [urgent_event(1), urgent_event(2, username="bob", reason="Oturum.")],
        "recommended_actions": ["investigate_further", "block_ioc_manual"],
        "data_gaps": [
            DataGap(
                source="DC-LAB-01",
                period_start=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
                period_end=datetime(2026, 10, 2, 10, 0, tzinfo=UTC),
                reason=DataGapReason.NO_DATA,
            )
        ],
        "case_url": CASE_URL,
    }
    return NoteContent.model_validate(values | changes)


def case_alert(*, content: NoteContent | None = None, **changes: object) -> CaseAlert:
    values: dict[str, object] = {
        "case_id": "case-12345",
        "offense_name": OFFENSE_NAME,
        "evaluated_at": T0,
        "content": note_content() if content is None else content,
    }
    return CaseAlert.model_validate(values | changes)


def evaluation(evaluation_no: int, level: str, **changes: object) -> CaseAlert:
    """The alert of the case's evaluation `evaluation_no` at notify level `level`."""
    return case_alert(
        content=note_content(evaluation_no=evaluation_no, notify_level=level, **changes)
    )


def group_alert(**changes: object) -> GroupAlert:
    values: dict[str, object] = {
        "case_id": f"group-{GROUP_ID}",
        "group_id": GROUP_ID,
        "title": "Multiple Login Failures for the Same User",
        "offense_count": 37,
        "evaluation_no": 1,
        "evaluated_at": T0,
        "verdict": "suspicious",
        "confidence": "medium",
        "notify_level": "high",
        "summary_tr": "Aynı kural 37 farklı kaynak için offense açtı.",
        "urgent_events": [urgent_event(1)],
        "recommended_actions": ["investigate_further"],
        "case_url": f"https://ais0c.example.com/cases/group-{GROUP_ID}",
    }
    return GroupAlert.model_validate(values | changes)


# --- database --------------------------------------------------------------------------------


async def allow_domains(sessions: Sessions, *domains: str) -> None:
    async with sessions.begin() as session:
        await session.execute(insert(AllowedEmailDomainRow), [{"domain": d} for d in domains])


async def add_recipients(sessions: Sessions, *addresses: str, list_name: str = "operators") -> None:
    async with sessions.begin() as session:
        await session.execute(
            insert(NotificationRecipientRow),
            [{"list_name": list_name, "email": address} for address in addresses],
        )


async def route(sessions: Sessions, *list_names: str, kind: EmailKind, level: str | None) -> None:
    """Point a notification kind and level at the named groups, as the admin does.

    The migration seeds `case_alert`/`group_alert` at high and critical and `hunt_report`, so a
    test changes what it needs instead of starting from nothing.
    """
    async with sessions.begin() as session:
        await session.execute(
            delete(NotificationRouteRow).where(
                NotificationRouteRow.kind == kind, NotificationRouteRow.level == level
            )
        )
        if list_names:
            await session.execute(
                insert(NotificationRouteRow),
                [{"kind": kind, "level": level, "list_name": name} for name in list_names],
            )


async def operators_in_example_com(sessions: Sessions) -> None:
    """The usual set-up: example.com allowed, two operators in it."""
    await allow_domains(sessions, "example.com")
    await add_recipients(sessions, *OPERATORS)


async def switch_writes(sessions: Sessions, enabled: bool, reason: str = "Canary starts.") -> None:
    """What an admin does from the UI, committed at once."""
    async with sessions.begin() as session:
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=enabled,
            reason=reason,
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )


async def notification(sessions: Sessions, idempotency_key: str) -> NotificationRow | None:
    async with sessions() as session:
        return await get_notification(session, idempotency_key)


async def notifications(sessions: Sessions) -> list[NotificationRow]:
    async with sessions() as session:
        return list(await session.scalars(select(NotificationRow).order_by(NotificationRow.id)))


async def email_audit(sessions: Sessions) -> list[AuditLogRow]:
    """The e-mail entries of `audit_log`, oldest first."""
    async with sessions() as session:
        entries = await list_audit(session, limit=1000)
    return [entry for entry in reversed(entries) if entry.action.startswith("email.")]


# --- a fake relay ----------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, transport: "FakeTransport") -> None:
        self._transport = transport

    async def send(self, message: EmailMessage, body: str) -> SendReceipt:
        transport = self._transport
        transport.attempts += 1
        if transport.delay:
            await asyncio.sleep(transport.delay)
        if transport.failures:
            raise transport.failures.pop(0)
        transport.sent.append((message, body))
        return SendReceipt(message_id=f"<fake.{len(transport.sent)}@example.com>")


class FakeTransport:
    """Stands in for the SMTP relay: keeps what was sent.

    `failures` are raised by the next sends, in order. `on_connect` runs once a connection is
    open, before anything is sent.
    """

    def __init__(
        self,
        *,
        failures: list[EmailTransportError] | None = None,
        connect_failure: EmailTransportError | None = None,
        on_connect: Callable[[], Awaitable[None]] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.sent: list[tuple[EmailMessage, str]] = []
        self.connections = 0
        self.attempts = 0
        self.failures = list(failures or [])
        self.connect_failure = connect_failure
        self.on_connect = on_connect
        self.delay = delay

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeConnection]:
        self.connections += 1
        if self.connect_failure is not None:
            raise self.connect_failure
        if self.on_connect is not None:
            await self.on_connect()
        yield FakeConnection(self)

    @property
    def messages(self) -> list[EmailMessage]:
        return [message for message, _ in self.sent]
