"""`audit_log`: append-only. A database trigger rejects UPDATE, DELETE and TRUNCATE, so there
is nothing here that changes an entry."""

from collections.abc import Mapping
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.enums import ActorKind
from ais0c_storage.models import AuditLogRow
from ais0c_storage.repositories._common import fetch_all, insert_row


async def append_audit(
    session: AsyncSession,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    details: Mapping[str, JsonValue] | None = None,
) -> AuditLogRow:
    """Append an entry, for example `action="catalog.rule.update"`.

    `at` is taken from the database clock (transaction time); a caller cannot backdate it.
    """
    values = dict(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=dict(details or {}),
    )
    return await insert_row(session, AuditLogRow, values)


async def list_audit(
    session: AsyncSession,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[AuditLogRow]:
    """Entries matching every given filter, newest first."""
    statement = select(AuditLogRow)
    if object_type is not None:
        statement = statement.where(AuditLogRow.object_type == object_type)
    if object_id is not None:
        statement = statement.where(AuditLogRow.object_id == object_id)
    if actor_id is not None:
        statement = statement.where(AuditLogRow.actor_id == actor_id)
    if action is not None:
        statement = statement.where(AuditLogRow.action == action)
    if since is not None:
        statement = statement.where(AuditLogRow.at >= since)
    if until is not None:
        statement = statement.where(AuditLogRow.at < until)
    statement = statement.order_by(AuditLogRow.id.desc()).limit(limit)
    return await fetch_all(session, statement)
