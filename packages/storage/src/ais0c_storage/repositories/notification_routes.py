"""The named recipient groups routed to a notification kind and level (D-41).

`replace_notification_routes` is what the admin API's `PUT /notification-routes` calls (T-028).
"""

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EmailKind, Level
from ais0c_storage.models import NotificationRouteRow
from ais0c_storage.repositories._common import fetch_all, insert_row


async def list_notification_route_groups(
    session: AsyncSession, *, kind: EmailKind, level: Level | None
) -> list[str]:
    """Return groups in ascending order, matching a hunt report's NULL level explicitly."""
    statement = (
        select(NotificationRouteRow.list_name)
        .where(NotificationRouteRow.kind == kind, NotificationRouteRow.level == level)
        .order_by(NotificationRouteRow.list_name)
    )
    return list(await session.scalars(statement))


async def list_notification_routes(session: AsyncSession) -> list[NotificationRouteRow]:
    """The whole routing table, ordered so the analyst UI can show it as it is stored.

    By kind, then by level with the hunt report's empty level last, then by group name.
    """
    statement = select(NotificationRouteRow).order_by(
        NotificationRouteRow.kind,
        NotificationRouteRow.level.nulls_last(),
        NotificationRouteRow.list_name,
    )
    return await fetch_all(session, statement)


async def replace_notification_routes(
    session: AsyncSession, routes: Iterable[tuple[EmailKind, Level | None, str]]
) -> list[NotificationRouteRow]:
    """Replace the whole routing table with `routes` (`PUT /notification-routes`, T-028).

    Each entry is a (kind, level, list_name). A repeated (kind, level, list_name) is written
    once. Group names are the admin's own names and the allowlist's business; the caller checks
    that every group exists before it calls this. The replacement is one transaction, so the
    executor never sees a half-written table.
    """
    await session.execute(delete(NotificationRouteRow))
    unique = dict.fromkeys((kind, level, list_name) for kind, level, list_name in routes)
    return [
        await insert_row(
            session,
            NotificationRouteRow,
            dict(kind=kind, level=level, list_name=list_name),
        )
        for kind, level, list_name in unique
    ]
