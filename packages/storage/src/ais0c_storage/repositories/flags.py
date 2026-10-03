"""`platform_flags`: switches such as the kill switch (architecture §26, T-23).

A flag without a row is off. `writes_enabled` is the kill switch: while it is off, the executor
writes nothing outside the platform. A new database has no row, so the platform starts in shadow
mode.

A flag changes only through `set_platform_flag`. It needs a reason and appends the change to
`audit_log` in the same transaction. An agent cannot change a flag.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.enums import ActorKind, PlatformFlag
from ais0c_storage.models import PlatformFlagRow
from ais0c_storage.repositories._common import get_row
from ais0c_storage.repositories.audit import append_audit

# `audit_log.action` and `audit_log.object_type` of a flag change; `object_id` is the flag name.
PLATFORM_FLAG_AUDIT_ACTION = "platform_flag.update"
PLATFORM_FLAG_OBJECT_TYPE = "platform_flag"


async def get_platform_flag(session: AsyncSession, name: PlatformFlag) -> PlatformFlagRow | None:
    """The flag as it is in the database now; None if it was never set, which means off."""
    return await get_row(session, PlatformFlagRow, PlatformFlag(name))


async def set_platform_flag(
    session: AsyncSession,
    name: PlatformFlag,
    *,
    enabled: bool,
    reason: str,
    actor_kind: ActorKind,
    actor_id: str,
) -> PlatformFlagRow:
    """Switch a flag on or off and append the change to `audit_log`.

    Both writes run in the caller's transaction, so a change is never committed without its
    audit entry. The entry records who (`actor_kind`, `actor_id`), when (`at`) and why
    (`details.reason`), with the new value and the one it replaced (`details.previous`, null
    if the flag had never been set). `changed_at` and `at` are the database's transaction time.
    Setting a flag to the value it already has is recorded the same way.

    Raises ValueError for a blank reason or actor, and when `actor_kind` is `agent`.
    """
    name = PlatformFlag(name)
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be True or False")
    reason = reason.strip()
    actor_id = actor_id.strip()
    if not reason:
        raise ValueError("a reason is required to change a platform flag")
    if not actor_id:
        raise ValueError("actor_id is required to change a platform flag")
    if ActorKind(actor_kind) is ActorKind.AGENT:
        raise ValueError("an agent cannot change a platform flag")

    # Locks the row: concurrent changes of a flag take turns, and each audit entry records the
    # value its own change replaced.
    previous = await session.scalar(
        select(PlatformFlagRow.enabled).where(PlatformFlagRow.name == name).with_for_update()
    )
    upsert = insert(PlatformFlagRow).values(
        name=name, enabled=enabled, reason=reason, changed_by=actor_id, changed_at=func.now()
    )
    statement = upsert.on_conflict_do_update(
        index_elements=[PlatformFlagRow.name],
        set_={
            "enabled": upsert.excluded.enabled,
            "reason": upsert.excluded.reason,
            "changed_by": upsert.excluded.changed_by,
            "changed_at": upsert.excluded.changed_at,
        },
    ).returning(PlatformFlagRow)
    result = await session.scalars(statement, execution_options={"populate_existing": True})
    row = result.one()
    await append_audit(
        session,
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=PLATFORM_FLAG_AUDIT_ACTION,
        object_type=PLATFORM_FLAG_OBJECT_TYPE,
        object_id=name.value,
        details={"enabled": enabled, "previous": previous, "reason": reason},
    )
    return row
