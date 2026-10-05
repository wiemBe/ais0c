"""Read the named recipient groups routed to a notification kind and level (D-41)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EmailKind, Level
from ais0c_storage.models import NotificationRouteRow


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
