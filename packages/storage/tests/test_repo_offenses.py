"""Repository functions of `offenses_seen` and `offense_groups` (criterion 6)."""

from datetime import timedelta

import anyio
import pytest
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from storage_payloads import T0, T1

from ais0c_contracts import CatalogMode
from ais0c_storage.db import create_session_factory
from ais0c_storage.enums import GroupStatus, OffenseStatus
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.repositories import (
    add_offense_seen,
    count_offenses,
    create_offense_group,
    find_offense_group,
    get_offense_group,
    get_offense_seen,
    increment_offense_group,
    list_pending_offenses,
    update_offense_group,
    update_offense_seen,
)

pytestmark = pytest.mark.anyio


async def add(
    session: AsyncSession,
    offense_id: int,
    *,
    pre_priority: int = 0,
    seen_after: timedelta = timedelta(0),
    status: OffenseStatus = OffenseStatus.PENDING,
    group_id: str | None = None,
) -> bool:
    return await add_offense_seen(
        session,
        offense_id=offense_id,
        first_seen_at=T0 + seen_after,
        last_updated_at=T0 + seen_after,
        description="Excessive Firewall Accepts From Single Source",
        rule_ids=[100201, 100305],
        catalog_mode=CatalogMode.ANALYZE,
        pre_priority=pre_priority,
        status=status,
        group_id=group_id,
    )


async def test_an_offense_is_recorded_once(session: AsyncSession) -> None:
    assert await add(session, 12345) is True
    assert await add(session, 12345, pre_priority=9, status=OffenseStatus.SKIPPED) is False
    await session.commit()

    stored = await get_offense_seen(session, 12345)

    assert stored is not None
    assert stored.status is OffenseStatus.PENDING
    assert stored.pre_priority == 0
    assert stored.rule_ids == [100201, 100305]
    assert stored.catalog_mode is CatalogMode.ANALYZE
    assert stored.first_seen_at == T0
    assert stored.first_seen_at.utcoffset() == timedelta(0)
    assert await get_offense_seen(session, 99999) is None


async def test_update_changes_only_the_given_fields(session: AsyncSession) -> None:
    await add(session, 12345)

    updated = await update_offense_seen(
        session, 12345, status=OffenseStatus.RUNNING, case_id="case-12345"
    )
    assert (updated.status, updated.case_id, updated.group_id) == (
        OffenseStatus.RUNNING,
        "case-12345",
        None,
    )
    updated = await update_offense_seen(
        session, 12345, last_updated_at=T1, rule_ids=[100201], description="Updated"
    )

    assert updated.status is OffenseStatus.RUNNING
    assert (updated.last_updated_at, updated.rule_ids, updated.description) == (
        T1,
        [100201],
        "Updated",
    )
    with pytest.raises(NotFoundError):
        await update_offense_seen(session, 99999, status=OffenseStatus.DONE)
    with pytest.raises(ValueError, match="no field"):
        await update_offense_seen(session, 12345)


async def test_an_offense_admitted_later_counts_as_first_seen_then(session: AsyncSession) -> None:
    """T-30 (5): a skipped offense admitted for analysis counts as first seen at that time, by
    the hourly group limit too; the intake changes it through `update_offense_seen`."""
    await add(session, 12345, status=OffenseStatus.SKIPPED)
    later = T1 + timedelta(hours=2)

    updated = await update_offense_seen(
        session, 12345, first_seen_at=later, status=OffenseStatus.PENDING
    )

    assert (updated.first_seen_at, updated.last_updated_at, updated.status) == (
        later,
        T0,
        OffenseStatus.PENDING,
    )
    assert await count_offenses(session, first_seen_since=later) == 1
    with pytest.raises(StatementError, match="naive datetime"):
        await update_offense_seen(session, 12345, first_seen_at=later.replace(tzinfo=None))


async def test_pending_offenses_come_by_priority_then_age(session: AsyncSession) -> None:
    await add(session, 1, pre_priority=1, seen_after=timedelta(minutes=1))
    await add(session, 2, pre_priority=5, seen_after=timedelta(minutes=3))
    await add(session, 3, pre_priority=5, seen_after=timedelta(minutes=2))
    await add(session, 4, pre_priority=9, status=OffenseStatus.SKIPPED)
    await add(session, 5, pre_priority=0)

    pending = await list_pending_offenses(session, limit=3)

    assert [offense.offense_id for offense in pending] == [3, 2, 1]


async def test_count_offenses_by_status_group_and_age(session: AsyncSession) -> None:
    await add(session, 1, status=OffenseStatus.RUNNING, group_id="G-1")
    await add(session, 2, status=OffenseStatus.DONE, group_id="G-1", seen_after=timedelta(hours=2))
    await add(session, 3, status=OffenseStatus.GROUPED, group_id="G-1")
    await add(session, 4, status=OffenseStatus.RUNNING)

    assert await count_offenses(session) == 4
    assert await count_offenses(session, statuses={OffenseStatus.RUNNING}) == 2
    analyzed = {OffenseStatus.RUNNING, OffenseStatus.DONE}
    assert await count_offenses(session, group_id="G-1", statuses=analyzed) == 2
    since = T0 + timedelta(hours=1)
    assert await count_offenses(session, group_id="G-1", first_seen_since=since) == 1


async def test_offense_group_lifecycle(session: AsyncSession) -> None:
    window_end = T0 + timedelta(hours=24)
    group = await create_offense_group(
        session,
        group_id="G-1",
        rule_set_hash="hash-a",
        window_start=T0,
        window_end=window_end,
    )
    assert (group.offense_count, group.status) == (0, GroupStatus.OPEN)
    with pytest.raises(DuplicateError):
        await create_offense_group(
            session,
            group_id="G-1",
            rule_set_hash="hash-a",
            window_start=T0,
            window_end=window_end,
        )

    group = await increment_offense_group(session, "G-1")
    group = await increment_offense_group(
        session, "G-1", window_end=window_end + timedelta(hours=1)
    )
    assert (group.offense_count, group.window_end) == (2, window_end + timedelta(hours=1))

    group = await update_offense_group(
        session, "G-1", status=GroupStatus.STORM, case_id="group-G-1"
    )
    assert (group.status, group.case_id) == (GroupStatus.STORM, "group-G-1")
    stored = await get_offense_group(session, "G-1")
    assert stored is not None
    assert stored.offense_count == 2
    with pytest.raises(NotFoundError):
        await increment_offense_group(session, "G-404")
    with pytest.raises(ValueError, match="no field"):
        await update_offense_group(session, "G-1")


async def test_find_group_uses_the_window_and_skips_closed_groups(session: AsyncSession) -> None:
    day = timedelta(hours=24)
    await create_offense_group(
        session, group_id="G-old", rule_set_hash="hash-a", window_start=T0 - day, window_end=T0
    )
    await create_offense_group(
        session, group_id="G-new", rule_set_hash="hash-a", window_start=T0, window_end=T0 + day
    )
    await create_offense_group(
        session,
        group_id="G-closed",
        rule_set_hash="hash-a",
        window_start=T0 + timedelta(hours=1),
        window_end=T0 + day,
        status=GroupStatus.CLOSED,
    )

    inside = await find_offense_group(session, rule_set_hash="hash-a", at=T0 + timedelta(hours=2))
    assert inside is not None
    assert inside.group_id == "G-new"
    assert await find_offense_group(session, rule_set_hash="hash-b", at=T0) is None
    assert await find_offense_group(session, rule_set_hash="hash-a", at=T0 + 2 * day) is None


async def test_concurrent_increments_are_not_lost(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    async with sessions.begin() as session:
        await create_offense_group(
            session,
            group_id="G-1",
            rule_set_hash="hash-a",
            window_start=T0,
            window_end=T1,
        )

    async def increment() -> None:
        async with sessions.begin() as session:
            await increment_offense_group(session, "G-1")

    async with sessions() as reader:
        # Loaded before the increments: later reads must not return this stale copy.
        assert await get_offense_group(reader, "G-1") is not None
        await reader.commit()

        async with anyio.create_task_group() as tasks:
            for _ in range(10):
                tasks.start_soon(increment)

        group = await get_offense_group(reader, "G-1")
    assert group is not None
    assert group.offense_count == 10
