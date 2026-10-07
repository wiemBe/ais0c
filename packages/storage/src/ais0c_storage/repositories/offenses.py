"""`offenses_seen`, `offense_groups` and `offense_group_values`: intake, repeat check and
grouping (architecture §9)."""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import CatalogMode
from ais0c_storage.enums import FullAnalysisReason, GroupStatus, GroupValueKind, OffenseStatus
from ais0c_storage.models import OffenseGroupRow, OffenseGroupValueRow, OffenseSeenRow
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
    full_analysis_reason: FullAnalysisReason | None = None,
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
            full_analysis_reason=full_analysis_reason,
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


async def set_full_analysis_reason(
    session: AsyncSession, offense_id: int, reason: FullAnalysisReason | None
) -> OffenseSeenRow:
    """Set why the offense got a full analysis; None for one its group took (T-027).

    Raises `NotFoundError` if the offense was never recorded.
    """
    statement = (
        update(OffenseSeenRow)
        .where(OffenseSeenRow.offense_id == offense_id)
        .values(full_analysis_reason=reason)
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
    full_analysis_reasons: Collection[FullAnalysisReason | None] | None = None,
) -> int:
    """Number of recorded offenses matching every given filter.

    Examples: running cases (`statuses={RUNNING}`), or full analyses a group started in the
    last hour (`group_id`, `statuses={RUNNING, DONE}`, `first_seen_since`). A None among
    `full_analysis_reasons` matches an offense without a reason.
    """
    statement = select(func.count()).select_from(OffenseSeenRow)
    if statuses is not None:
        statement = statement.where(OffenseSeenRow.status.in_(list(statuses)))
    if group_id is not None:
        statement = statement.where(OffenseSeenRow.group_id == group_id)
    if first_seen_since is not None:
        statement = statement.where(OffenseSeenRow.first_seen_at >= first_seen_since)
    if full_analysis_reasons is not None:
        statement = statement.where(_reason_in(full_analysis_reasons))
    return await session.scalar(statement) or 0


async def latest_offense_update(session: AsyncSession) -> datetime | None:
    """The newest `last_updated_at` of any recorded offense, the platform's own view of how
    current its intake is (T-032); None when no offense was recorded."""
    return await session.scalar(select(func.max(OffenseSeenRow.last_updated_at)))


def _reason_in(reasons: Collection[FullAnalysisReason | None]) -> ColumnElement[bool]:
    column = OffenseSeenRow.full_analysis_reason
    named = [reason for reason in reasons if reason is not None]
    conditions: list[ColumnElement[bool]] = [column.in_(named)] if named else []
    if None in reasons:
        conditions.append(column.is_(None))
    return or_(*conditions) if conditions else column.in_([])


async def list_group_offenses(
    session: AsyncSession, group_id: str, *, statuses: Collection[OffenseStatus] | None = None
) -> list[OffenseSeenRow]:
    """The group's offenses, oldest first (`first_seen_at`, then `offense_id`)."""
    statement = select(OffenseSeenRow).where(OffenseSeenRow.group_id == group_id)
    if statuses is not None:
        statement = statement.where(OffenseSeenRow.status.in_(list(statuses)))
    statement = statement.order_by(OffenseSeenRow.first_seen_at, OffenseSeenRow.offense_id)
    return await fetch_all(session, statement)


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


@dataclass(frozen=True)
class GroupCursor:
    """Where a group page ended: the row's own `window_start` and `group_id`."""

    window_start: datetime
    group_id: str


async def list_offense_groups(
    session: AsyncSession,
    *,
    statuses: Collection[GroupStatus] | None = None,
    window_from: datetime | None = None,
    window_to: datetime | None = None,
    after: GroupCursor | None = None,
    limit: int = 50,
) -> list[OffenseGroupRow]:
    """Groups matching every given filter, newest window first.

    `after` is the last row of the previous page (`newest_group_cursor`): the page starts after
    it in the same order, so a page never repeats or skips a row whose `window_start` is the
    same as its neighbours'.
    """
    statement = select(OffenseGroupRow)
    if statuses is not None:
        statement = statement.where(OffenseGroupRow.status.in_(list(statuses)))
    if window_from is not None:
        statement = statement.where(OffenseGroupRow.window_start >= window_from)
    if window_to is not None:
        statement = statement.where(OffenseGroupRow.window_start < window_to)
    if after is not None:
        # `window_start` descending, then `group_id` ascending: see `list_cases`.
        statement = statement.where(
            or_(
                OffenseGroupRow.window_start < after.window_start,
                and_(
                    OffenseGroupRow.window_start == after.window_start,
                    OffenseGroupRow.group_id > after.group_id,
                ),
            )
        )
    statement = statement.order_by(OffenseGroupRow.window_start.desc(), OffenseGroupRow.group_id)
    return await fetch_all(session, statement.limit(limit))


def newest_group_cursor(row: OffenseGroupRow) -> GroupCursor:
    return GroupCursor(window_start=row.window_start, group_id=row.group_id)


async def list_offenses_by_ids(
    session: AsyncSession, offense_ids: Collection[int]
) -> dict[int, OffenseSeenRow]:
    """The recorded offenses among `offense_ids`, by offense ID; unknown IDs are left out.

    One query for a whole page of cases, so the analyst API can put each row's rule IDs in its
    answer without a query per case (T-028).
    """
    wanted = set(offense_ids)
    if not wanted:
        return {}
    statement = select(OffenseSeenRow).where(OffenseSeenRow.offense_id.in_(list(wanted)))
    return {row.offense_id: row for row in await fetch_all(session, statement)}


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


async def close_ended_offense_group(
    session: AsyncSession, group_id: str, *, at: datetime
) -> OffenseGroupRow | None:
    """Close the group if its window ended before `at`; returns the group as it is now, or None
    if it does not exist.

    One conditional statement, so an intake that moves the window in the meantime keeps the
    group open: its UPDATE either lands first, and the window has not ended, or waits for this
    one and then finds the group closed, like any closed group.
    """
    await session.execute(
        update(OffenseGroupRow)
        .where(
            OffenseGroupRow.group_id == group_id,
            OffenseGroupRow.status != GroupStatus.CLOSED,
            OffenseGroupRow.window_end < at,
        )
        .values(status=GroupStatus.CLOSED)
    )
    return await get_offense_group(session, group_id)


# --- offense_group_values (T-027) ----------------------------------------------------------


async def add_group_values(
    session: AsyncSession,
    group_id: str,
    offense_id: int,
    values: Mapping[GroupValueKind, Iterable[str]],
    *,
    seen_at: datetime,
) -> None:
    """Record the values the offense carries in its group; a value recorded for the offense
    before is left as it is."""
    rows = [
        {
            "group_id": group_id,
            "kind": kind,
            "value": value,
            "offense_id": offense_id,
            "seen_at": seen_at,
        }
        for kind, items in values.items()
        for value in dict.fromkeys(items)
    ]
    if not rows:
        return
    await session.execute(insert(OffenseGroupValueRow).values(rows).on_conflict_do_nothing())


async def seen_group_values(
    session: AsyncSession, group_id: str, values: Mapping[GroupValueKind, Collection[str]]
) -> set[tuple[GroupValueKind, str]]:
    """Which of `values` an offense of the group carried before, as (kind, value) pairs."""
    conditions = [
        (OffenseGroupValueRow.kind == kind) & OffenseGroupValueRow.value.in_(list(items))
        for kind, items in values.items()
        if items
    ]
    if not conditions:
        return set()
    statement = (
        select(OffenseGroupValueRow.kind, OffenseGroupValueRow.value)
        .where(OffenseGroupValueRow.group_id == group_id, or_(*conditions))
        .distinct()
    )
    return {(kind, value) for kind, value in (await session.execute(statement)).all()}


@dataclass(frozen=True)
class GroupValueCounts:
    """One kind of a group's values: how many different values its offenses carry, and the
    most frequent ones with the number of offenses that carry each."""

    distinct: int
    top: list[tuple[str, int]]


async def count_group_values(
    session: AsyncSession,
    group_id: str,
    *,
    top: int,
    statuses: Collection[OffenseStatus],
) -> dict[GroupValueKind, GroupValueCounts]:
    """The group's values of every kind, counted over its offenses in `statuses`.

    The most frequent come first; values carried by as many offenses come in text order, so
    the same rows give the same answer. A kind no offense carries is missing.
    """
    member = (
        select(OffenseGroupValueRow.kind, OffenseGroupValueRow.value)
        .join(OffenseSeenRow, OffenseSeenRow.offense_id == OffenseGroupValueRow.offense_id)
        .where(
            OffenseGroupValueRow.group_id == group_id,
            OffenseSeenRow.status.in_(list(statuses)),
        )
        .subquery()
    )
    counted = (
        select(
            member.c.kind,
            member.c.value,
            func.count().label("offenses"),
            func.row_number()
            .over(
                partition_by=member.c.kind,
                order_by=(func.count().desc(), member.c.value),
            )
            .label("place"),
        )
        .group_by(member.c.kind, member.c.value)
        .subquery()
    )
    distinct = select(counted.c.kind, func.count()).group_by(counted.c.kind)
    leading = (
        select(counted.c.kind, counted.c.value, counted.c.offenses)
        .where(counted.c.place <= top)
        .order_by(counted.c.kind, counted.c.place)
    )
    totals = {
        GroupValueKind(kind): number for kind, number in (await session.execute(distinct)).all()
    }
    tops: dict[GroupValueKind, list[tuple[str, int]]] = {kind: [] for kind in totals}
    for kind, value, offenses in (await session.execute(leading)).all():
        tops[GroupValueKind(kind)].append((value, offenses))
    return {kind: GroupValueCounts(distinct=totals[kind], top=tops[kind]) for kind in totals}
