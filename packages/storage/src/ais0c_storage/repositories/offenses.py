"""`offenses_seen` and `offense_groups`: intake, repeat check and grouping (architecture §9)."""

from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import CatalogMode
from ais0c_storage.enums import GroupStatus, OffenseStatus
from ais0c_storage.models import OffenseGroupRow, OffenseSeenRow
from ais0c_storage.repositories._common import fetch_all, fetch_one, get_row, insert_new, update_one


async def add_offense_seen(
    session: AsyncSession,
    *,
    offense_id: int,
    first_seen_at: datetime,
    last_updated_at: datetime,
    description: str,
    rule_ids: Sequence[int],
    catalog_mode: CatalogMode,
    pre_priority: int,
    status: OffenseStatus = OffenseStatus.PENDING,
    group_id: str | None = None,
    case_id: str | None = None,
) -> bool:
    """Record an offense the first time it is seen.

    Returns False, and changes nothing, if the offense was already recorded: the repeat check.
    """
    statement = (
        insert(OffenseSeenRow)
        .values(
            offense_id=offense_id,
            first_seen_at=first_seen_at,
            last_updated_at=last_updated_at,
            description=description,
            rule_ids=list(rule_ids),
            catalog_mode=catalog_mode,
            pre_priority=pre_priority,
            status=status,
            group_id=group_id,
            case_id=case_id,
        )
        .on_conflict_do_nothing(index_elements=[OffenseSeenRow.offense_id])
        .returning(OffenseSeenRow.offense_id)
    )
    inserted = await session.scalar(statement)
    return inserted is not None


async def get_offense_seen(session: AsyncSession, offense_id: int) -> OffenseSeenRow | None:
    return await get_row(session, OffenseSeenRow, offense_id)


async def update_offense_seen(
    session: AsyncSession,
    offense_id: int,
    *,
    status: OffenseStatus | None = None,
    case_id: str | None = None,
    group_id: str | None = None,
    first_seen_at: datetime | None = None,
    last_updated_at: datetime | None = None,
    description: str | None = None,
    rule_ids: Sequence[int] | None = None,
    catalog_mode: CatalogMode | None = None,
    pre_priority: int | None = None,
) -> OffenseSeenRow:
    """Change the given fields; a field left as None keeps its value.

    `first_seen_at` changes when a skipped offense is admitted for analysis: it then counts as
    first seen at that time (T-30). Raises `NotFoundError` if the offense was never recorded.
    """
    changes = {
        "status": status,
        "case_id": case_id,
        "group_id": group_id,
        "first_seen_at": first_seen_at,
        "last_updated_at": last_updated_at,
        "description": description,
        "rule_ids": None if rule_ids is None else list(rule_ids),
        "catalog_mode": catalog_mode,
        "pre_priority": pre_priority,
    }
    values = {name: value for name, value in changes.items() if value is not None}
    if not values:
        raise ValueError("no field to change")
    statement = (
        update(OffenseSeenRow).where(OffenseSeenRow.offense_id == offense_id).values(**values)
    )
    return await update_one(session, statement, OffenseSeenRow, f"offense {offense_id}")


async def list_pending_offenses(session: AsyncSession, *, limit: int) -> list[OffenseSeenRow]:
    """Offenses waiting for a case, highest `pre_priority` first, then oldest first."""
    statement = (
        select(OffenseSeenRow)
        .where(OffenseSeenRow.status == OffenseStatus.PENDING)
        .order_by(
            OffenseSeenRow.pre_priority.desc(),
            OffenseSeenRow.first_seen_at,
            OffenseSeenRow.offense_id,
        )
        .limit(limit)
    )
    return await fetch_all(session, statement)


async def count_offenses(
    session: AsyncSession,
    *,
    statuses: Collection[OffenseStatus] | None = None,
    group_id: str | None = None,
    first_seen_since: datetime | None = None,
) -> int:
    """Number of recorded offenses matching every given filter.

    Examples: running cases (`statuses={RUNNING}`), or full analyses a group started in the
    last hour (`group_id`, `statuses={RUNNING, DONE}`, `first_seen_since`).
    """
    statement = select(func.count()).select_from(OffenseSeenRow)
    if statuses is not None:
        statement = statement.where(OffenseSeenRow.status.in_(list(statuses)))
    if group_id is not None:
        statement = statement.where(OffenseSeenRow.group_id == group_id)
    if first_seen_since is not None:
        statement = statement.where(OffenseSeenRow.first_seen_at >= first_seen_since)
    return await session.scalar(statement) or 0


async def create_offense_group(
    session: AsyncSession,
    *,
    group_id: str,
    rule_set_hash: str,
    window_start: datetime,
    window_end: datetime,
    offense_count: int = 0,
    status: GroupStatus = GroupStatus.OPEN,
) -> OffenseGroupRow:
    """Raises `DuplicateError` if the group exists."""
    values = dict(
        group_id=group_id,
        rule_set_hash=rule_set_hash,
        window_start=window_start,
        window_end=window_end,
        offense_count=offense_count,
        status=status,
    )
    return await insert_new(session, OffenseGroupRow, values, f"offense group {group_id!r}")


async def get_offense_group(session: AsyncSession, group_id: str) -> OffenseGroupRow | None:
    return await get_row(session, OffenseGroupRow, group_id)


async def find_offense_group(
    session: AsyncSession, *, rule_set_hash: str, at: datetime
) -> OffenseGroupRow | None:
    """The newest group of this rule set that is not closed and whose window contains `at`."""
    statement = (
        select(OffenseGroupRow)
        .where(
            OffenseGroupRow.rule_set_hash == rule_set_hash,
            OffenseGroupRow.status != GroupStatus.CLOSED,
            OffenseGroupRow.window_start <= at,
            OffenseGroupRow.window_end >= at,
        )
        .order_by(OffenseGroupRow.window_start.desc())
        .limit(1)
    )
    return await fetch_one(session, statement)


async def increment_offense_group(
    session: AsyncSession, group_id: str, *, window_end: datetime | None = None
) -> OffenseGroupRow:
    """Add one to `offense_count` in a single statement, so concurrent intakes do not lose
    counts; also move `window_end` when given (sliding window)."""
    statement = (
        update(OffenseGroupRow)
        .where(OffenseGroupRow.group_id == group_id)
        .values(offense_count=OffenseGroupRow.offense_count + 1)
    )
    if window_end is not None:
        statement = statement.values(window_end=window_end)
    return await update_one(session, statement, OffenseGroupRow, f"offense group {group_id!r}")


async def update_offense_group(
    session: AsyncSession,
    group_id: str,
    *,
    status: GroupStatus | None = None,
    case_id: str | None = None,
    window_end: datetime | None = None,
) -> OffenseGroupRow:
    """Change the given fields; a field left as None keeps its value."""
    changes = {"status": status, "case_id": case_id, "window_end": window_end}
    values = {name: value for name, value in changes.items() if value is not None}
    if not values:
        raise ValueError("no field to change")
    statement = update(OffenseGroupRow).where(OffenseGroupRow.group_id == group_id).values(**values)
    return await update_one(session, statement, OffenseGroupRow, f"offense group {group_id!r}")
