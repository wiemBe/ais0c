"""Intake activities on a real database (criteria 2, 3, 4 and 5 at the activity level)."""

from datetime import datetime, timedelta

import pytest
from activity_db import case, catalog_rule, critical_asset, group, seen, start_case_row
from activity_payloads import T0, offense
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CaseSettings,
    FakeOffenseSource,
    IntakeActivities,
    SessionFactory,
    pre_priority,
    rule_set_hash,
)
from ais0c_activities.grouping import new_group_id
from ais0c_contracts import CatalogMode, Level, OffenseSnapshot
from ais0c_storage.enums import CaseStatus, CriticalAssetKind, GroupStatus, OffenseStatus
from ais0c_storage.repositories import update_offense_seen

pytestmark = pytest.mark.anyio

NOW = T0 + timedelta(minutes=5)
NOISY_RULE = 100201
SKIP_RULE = 900001


@pytest.fixture
def source() -> FakeOffenseSource:
    return FakeOffenseSource()


@pytest.fixture
def intake(sessions: SessionFactory, source: FakeOffenseSource) -> IntakeActivities:
    return IntakeActivities(
        sessions=sessions, source=source, settings=CaseSettings(max_concurrent_cases=2)
    )


async def admit(
    intake: IntakeActivities, *offenses: OffenseSnapshot, at: datetime = NOW
) -> list[int]:
    return await ActivityEnvironment().run(intake.admit_offenses, list(offenses), at)


async def test_a_new_offense_is_recorded_pending_in_its_rule_set_group(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    assert await admit(intake, offense(1)) == []

    row = await seen(sessions, 1)
    assert row is not None
    assert (row.status, row.catalog_mode, row.case_id) == (
        OffenseStatus.PENDING,
        CatalogMode.ANALYZE,
        None,
    )
    assert (row.first_seen_at, row.last_updated_at) == (NOW, T0)
    assert row.group_id is not None
    assert row.group_id == new_group_id(rule_set_hash([NOISY_RULE]), NOW)
    stored_group = await group(sessions, row.group_id)
    assert stored_group is not None
    assert (stored_group.offense_count, stored_group.status) == (1, GroupStatus.OPEN)
    assert (stored_group.window_start, stored_group.window_end) == (NOW, NOW + timedelta(hours=24))


async def test_an_offense_of_skipped_rules_is_recorded_as_skipped(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await catalog_rule(sessions, SKIP_RULE, mode=CatalogMode.SKIP)

    await admit(intake, offense(2, rule_ids=[SKIP_RULE]))

    row = await seen(sessions, 2)
    assert row is not None
    assert (row.status, row.catalog_mode, row.group_id) == (
        OffenseStatus.SKIPPED,
        CatalogMode.SKIP,
        None,
    )
    assert await ActivityEnvironment().run(intake.next_pending_offenses) == []


async def test_a_skipped_rule_does_not_hide_an_analyzed_one(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await catalog_rule(sessions, SKIP_RULE, mode=CatalogMode.SKIP)

    await admit(intake, offense(3, rule_ids=[SKIP_RULE, NOISY_RULE]))

    row = await seen(sessions, 3)
    assert row is not None
    assert row.status is OffenseStatus.PENDING


async def test_the_same_offense_is_recorded_once(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await admit(intake, offense(4), offense(4))
    await admit(intake, offense(4), at=NOW + timedelta(minutes=1))

    row = await seen(sessions, 4)
    assert row is not None
    assert row.first_seen_at == NOW
    assert row.group_id is not None
    stored_group = await group(sessions, row.group_id)
    assert stored_group is not None
    assert stored_group.offense_count == 1


async def test_a_change_is_reported_only_for_an_offense_with_an_open_case(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await admit(intake, offense(5))
    v2 = T0 + timedelta(minutes=2)
    assert await admit(intake, offense(5, updated=v2)) == []  # pending: its case starts later
    row = await seen(sessions, 5)
    assert row is not None
    assert row.last_updated_at == v2

    await start_case_row(sessions, 5)
    v3 = T0 + timedelta(minutes=3)
    assert await admit(intake, offense(5, updated=v3)) == [5]
    assert await admit(intake, offense(5, updated=v3)) == [5]  # a retry reports it again
    assert await admit(intake, offense(5, updated=v2)) == []  # an older copy is ignored
    row = await seen(sessions, 5)
    assert row is not None
    assert row.last_updated_at == v3


async def test_a_storm_is_grouped_after_n_full_analyses_an_hour(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await critical_asset(sessions, CriticalAssetKind.IP, "192.0.2.50", "SWIFT")
    storm = [offense(n, destination_ips=[f"198.51.100.{n}"]) for n in range(10, 17)]
    hits_swift = offense(17, destination_ips=["192.0.2.50"])

    for item in storm:
        await admit(intake, item)
    await admit(intake, hits_swift)
    rows = [await seen(sessions, n) for n in range(10, 18)]

    statuses = [row.status for row in rows if row is not None]
    assert statuses == [OffenseStatus.PENDING] * 5 + [OffenseStatus.GROUPED] * 2 + [
        OffenseStatus.PENDING
    ]
    group_ids = {row.group_id for row in rows if row is not None}
    assert len(group_ids) == 1
    stored_group = await group(sessions, group_ids.pop() or "")
    assert stored_group is not None
    assert (stored_group.status, stored_group.offense_count) == (GroupStatus.STORM, 8)

    # An hour later the group has room for full analyses again.
    await admit(intake, offense(18), at=NOW + timedelta(minutes=61))
    later = await seen(sessions, 18)
    assert later is not None
    assert later.status is OffenseStatus.PENDING
    assert later.group_id == stored_group.group_id


async def test_a_group_ends_24_hours_after_its_last_offense(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await admit(intake, offense(20))
    await admit(intake, offense(21), at=NOW + timedelta(hours=20))
    await admit(intake, offense(22), at=NOW + timedelta(hours=44, seconds=1))

    rows = [await seen(sessions, n) for n in (20, 21, 22)]
    group_ids = [row.group_id for row in rows if row is not None]
    assert group_ids[0] == group_ids[1] != group_ids[2]


async def test_pending_offenses_start_by_pre_priority_within_the_case_limit(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await catalog_rule(sessions, 10, min_level=Level.CRITICAL)
    await catalog_rule(sessions, 11, min_level=Level.HIGH)
    await critical_asset(sessions, CriticalAssetKind.IP, "192.0.2.50", "SWIFT")
    plain = offense(30, rule_ids=[12])
    asset_hit = offense(31, rule_ids=[12], destination_ips=["192.0.2.50"])
    floor_high = offense(32, rule_ids=[11])
    floor_critical = offense(33, rule_ids=[10])
    await admit(intake, plain, asset_hit, floor_high, floor_critical)

    rows = {n: await seen(sessions, n) for n in (30, 31, 32, 33)}
    priorities = {n: row.pre_priority for n, row in rows.items() if row is not None}
    assert priorities == {
        30: pre_priority(catalog_floor=None, critical_asset_hit=False, ioc_hit=False),
        31: pre_priority(catalog_floor=None, critical_asset_hit=True, ioc_hit=False),
        32: pre_priority(catalog_floor=Level.HIGH, critical_asset_hit=False, ioc_hit=False),
        33: pre_priority(catalog_floor=Level.CRITICAL, critical_asset_hit=False, ioc_hit=False),
    }

    async def next_pending() -> list[int]:
        return await ActivityEnvironment().run(intake.next_pending_offenses)

    assert await next_pending() == [33, 32]  # the limit is 2
    await start_case_row(sessions, 33)
    assert await next_pending() == [32]
    await start_case_row(sessions, 32, status=CaseStatus.DECIDED)  # decided cases do not count
    assert await next_pending() == [31]


async def test_a_started_case_without_its_row_still_counts(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await admit(intake, offense(40), offense(41), offense(42, rule_ids=[7]))
    await start_case_row(sessions, 40)
    # Started, but the case workflow has not opened its row yet.
    async with sessions.begin() as session:
        await update_offense_seen(session, 41, status=OffenseStatus.RUNNING, case_id="case-41")

    assert await ActivityEnvironment().run(intake.next_pending_offenses) == []
    assert await case(sessions, "case-41") is None


async def test_closed_offenses_are_found_among_the_open_cases(
    sessions: SessionFactory, intake: IntakeActivities, source: FakeOffenseSource
) -> None:
    await admit(intake, offense(50), offense(51), offense(52))
    await start_case_row(sessions, 50)
    await start_case_row(sessions, 51)
    for offense_id in (51, 52):
        source.close(offense_id)

    assert await ActivityEnvironment().run(intake.find_closed_offenses) == [51]


async def test_changed_offenses_come_from_the_source_in_cursor_order(
    intake: IntakeActivities, source: FakeOffenseSource
) -> None:
    for item in (offense(62), offense(61), offense(60, start=T0 + timedelta(minutes=1))):
        source.put(item)
    source.put(offense(63))
    source.close(63)

    page = await ActivityEnvironment().run(intake.fetch_offense_changes, T0, 61, 10)

    assert [item.offense_id for item in page] == [62, 60]
