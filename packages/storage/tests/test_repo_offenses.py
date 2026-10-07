"""Repository functions of `offenses_seen`, `offense_groups` (criterion 6) and
`offense_group_values` (T-027). IPs are from the RFC 5737 ranges."""

from datetime import timedelta

import anyio
import pytest
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from storage_payloads import T0, T1

from ais0c_contracts import CatalogMode
from ais0c_storage.db import create_session_factory
from ais0c_storage.enums import FullAnalysisReason, GroupStatus, GroupValueKind, OffenseStatus
from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.repositories import (
    GroupValueCounts,
    add_group_values,
    add_offense_seen,
    close_ended_offense_group,
    count_group_values,
    count_offenses,
    create_offense_group,
    find_offense_group,
    get_offense_group,
    get_offense_seen,
    increment_offense_group,
    list_group_offenses,
    list_pending_offenses,
    seen_group_values,
    set_full_analysis_reason,
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
    reason: FullAnalysisReason | None = None,
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
        full_analysis_reason=reason,
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


# --- T-027: full analysis reasons, the group's offenses and values, closing a group ----------


async def storm_group(session: AsyncSession, group_id: str = "G-1") -> None:
    await create_offense_group(
        session,
        group_id=group_id,
        rule_set_hash="hash-a",
        window_start=T0,
        window_end=T0 + timedelta(hours=24),
        status=GroupStatus.STORM,
    )


async def test_count_offenses_by_full_analysis_reason(session: AsyncSession) -> None:
    """The hourly limit counts the limit's and the exempt offenses and those recorded before
    the reason existed; the novelty escapes and the sample count on their own."""
    await add(session, 1, status=OffenseStatus.DONE, group_id="G-1")
    await add(session, 2, group_id="G-1", reason=FullAnalysisReason.LIMIT)
    await add(session, 3, group_id="G-1", reason=FullAnalysisReason.EXEMPT)
    await add(session, 4, group_id="G-1", reason=FullAnalysisReason.NOVELTY)
    await add(session, 5, group_id="G-1", reason=FullAnalysisReason.SAMPLE)

    limit = {None, FullAnalysisReason.LIMIT, FullAnalysisReason.EXEMPT}
    assert await count_offenses(session, group_id="G-1", full_analysis_reasons=limit) == 3
    novelty = {FullAnalysisReason.NOVELTY}
    assert await count_offenses(session, group_id="G-1", full_analysis_reasons=novelty) == 1
    assert await count_offenses(session, full_analysis_reasons=set()) == 0

    updated = await set_full_analysis_reason(session, 4, None)
    assert updated.full_analysis_reason is None
    assert await count_offenses(session, group_id="G-1", full_analysis_reasons=novelty) == 0
    with pytest.raises(NotFoundError):
        await set_full_analysis_reason(session, 99999, FullAnalysisReason.LIMIT)


async def test_the_group_offenses_come_oldest_first(session: AsyncSession) -> None:
    await add(session, 7, status=OffenseStatus.GROUPED, group_id="G-1", seen_after=timedelta(2))
    await add(session, 9, status=OffenseStatus.GROUPED, group_id="G-1")
    await add(session, 8, status=OffenseStatus.DONE, group_id="G-1")
    await add(session, 6, status=OffenseStatus.GROUPED, group_id="G-2")

    every = await list_group_offenses(session, "G-1")
    grouped = await list_group_offenses(session, "G-1", statuses={OffenseStatus.GROUPED})

    assert [row.offense_id for row in every] == [8, 9, 7]
    assert [row.offense_id for row in grouped] == [9, 7]


async def test_group_values_are_recorded_once_per_offense(session: AsyncSession) -> None:
    await storm_group(session)
    await add(session, 1, status=OffenseStatus.GROUPED, group_id="G-1")
    values = {
        GroupValueKind.SOURCE_IP: ["203.0.113.7", "203.0.113.7"],
        GroupValueKind.LOG_SOURCE: ["112"],
        GroupValueKind.USERNAME: [],
    }

    await add_group_values(session, "G-1", 1, values, seen_at=T0)
    await add_group_values(session, "G-1", 1, values, seen_at=T1)
    await add_group_values(session, "G-1", 1, {}, seen_at=T1)

    seen = await seen_group_values(
        session,
        "G-1",
        {
            GroupValueKind.LOG_SOURCE: ["112", "113"],
            GroupValueKind.CATEGORY: ["Firewall Permit"],
            GroupValueKind.USERNAME: [],
        },
    )
    assert seen == {(GroupValueKind.LOG_SOURCE, "112")}
    assert await seen_group_values(session, "G-1", {}) == set()
    assert await seen_group_values(session, "G-2", {GroupValueKind.LOG_SOURCE: ["112"]}) == set()


async def test_group_value_counts_give_the_most_frequent_values(session: AsyncSession) -> None:
    await storm_group(session)
    sources = {1: ["203.0.113.7"], 2: ["203.0.113.7", "203.0.113.9"], 3: ["203.0.113.8"]}
    for offense_id, addresses in sources.items():
        await add(session, offense_id, status=OffenseStatus.GROUPED, group_id="G-1")
        await add_group_values(
            session,
            "G-1",
            offense_id,
            {GroupValueKind.SOURCE_IP: addresses, GroupValueKind.LOG_SOURCE: ["112"]},
            seen_at=T0,
        )
    # A skipped offense's values do not count.
    await add(session, 4, status=OffenseStatus.SKIPPED, group_id="G-1")
    await add_group_values(
        session, "G-1", 4, {GroupValueKind.SOURCE_IP: ["203.0.113.8"]}, seen_at=T0
    )
    counted = set(OffenseStatus) - {OffenseStatus.SKIPPED}

    counts = await count_group_values(session, "G-1", top=2, statuses=counted)

    assert counts == {
        GroupValueKind.SOURCE_IP: GroupValueCounts(
            distinct=3, top=[("203.0.113.7", 2), ("203.0.113.8", 1)]
        ),
        GroupValueKind.LOG_SOURCE: GroupValueCounts(distinct=1, top=[("112", 3)]),
    }
    assert await count_group_values(session, "G-2", top=2, statuses=counted) == {}


async def test_a_group_closes_only_after_its_window(session: AsyncSession) -> None:
    await storm_group(session)
    window_end = T0 + timedelta(hours=24)

    still_open = await close_ended_offense_group(session, "G-1", at=window_end)
    assert still_open is not None
    assert still_open.status is GroupStatus.STORM

    closed = await close_ended_offense_group(session, "G-1", at=window_end + timedelta(seconds=1))
    assert closed is not None
    assert closed.status is GroupStatus.CLOSED
    assert await close_ended_offense_group(session, "G-404", at=window_end) is None
