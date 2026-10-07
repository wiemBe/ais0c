"""The activities of a group's case on a real database (T-027 criteria 1 and 7): the group's
deterministic summary and the offenses it took, the catalog check of those offenses, the
group's enrichment and floor, the case's SLA, and closing the group when its window ends.

The group is made the way the intake makes it: N offenses get a full analysis, the next ones
join the group. IPs are from the RFC 5737 ranges.
"""

from datetime import datetime, timedelta

import pytest
from activity_db import case, catalog_log_source, catalog_rule, group, seen
from activity_payloads import T0, offense
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CaseSettings,
    FakeOffenseSource,
    GroupCaseActivities,
    IntakeActivities,
    SessionFactory,
    rule_set_hash,
    sample_chosen,
)
from ais0c_activities.grouping import new_group_id
from ais0c_agents import GroupSummary, GroupValueCount
from ais0c_contracts import CaseSource, CatalogMode, Level
from ais0c_storage.enums import CaseStatus, GroupStatus, OffenseStatus

pytestmark = pytest.mark.anyio

NOW = T0 + timedelta(minutes=5)
RULE = 100201
LIMIT = 2
GROUP_ID = new_group_id(rule_set_hash([RULE]), NOW)
CASE_ID = f"group-{GROUP_ID}"
SETTINGS = CaseSettings(
    case_url_base="https://ais0c.example.com/cases",
    group_full_analyses_per_hour=LIMIT,
    group_settle=timedelta(minutes=7),
)


@pytest.fixture
def groups(sessions: SessionFactory) -> GroupCaseActivities:
    return GroupCaseActivities(sessions=sessions, settings=SETTINGS)


def unsampled(count: int, start: int = 1) -> list[int]:
    found: list[int] = []
    number = start
    while len(found) < count:
        if not sample_chosen(GROUP_ID, NOW, number):
            found.append(number)
        number += 1
    return found


async def make_storm(sessions: SessionFactory) -> tuple[list[int], list[int]]:
    """Two full analyses, then four offenses the group takes: (analyzed, grouped)."""
    intake = IntakeActivities(sessions=sessions, source=FakeOffenseSource(), settings=SETTINGS)
    ids = unsampled(6)
    sources = ["203.0.113.7", "203.0.113.7", "203.0.113.8", "203.0.113.7", "203.0.113.9", "x"]
    for minute, (offense_id, source) in enumerate(zip(ids, sources, strict=True)):
        item = offense(
            offense_id,
            source_ips=[source],
            destination_ips=["198.51.100.15"],
            usernames=["svc_backup_7731"] if offense_id % 2 else [],
        )
        await ActivityEnvironment().run(
            intake.admit_offenses, [item], NOW + timedelta(minutes=minute)
        )
    return ids[:LIMIT], ids[LIMIT:]


async def state(groups: GroupCaseActivities) -> tuple[GroupSummary | None, list[int], object]:
    return await ActivityEnvironment().run(groups.group_case_state, GROUP_ID)


async def test_the_summary_counts_every_offense_of_the_group(
    sessions: SessionFactory, groups: GroupCaseActivities
) -> None:
    """Criterion 1: offense count and time range, the rules, and for each kind of value the
    most frequent with their counts and the number of different values."""
    await catalog_rule(sessions, RULE)
    analyzed, grouped = await make_storm(sessions)

    summary, taken, window_end = await state(groups)

    assert taken == grouped
    assert window_end == NOW + timedelta(minutes=5) + timedelta(hours=24)
    assert summary is not None
    assert summary.offense_count == 6
    assert (summary.first_seen_at, summary.last_seen_at) == (NOW, NOW + timedelta(minutes=5))
    assert summary.example_offense_id == grouped[0]
    assert [(rule.rule_id, rule.name) for rule in summary.rules] == [(RULE, f"Rule {RULE}")]
    assert summary.source_ips.distinct == 4
    assert summary.source_ips.top[0] == GroupValueCount(value="203.0.113.7", offenses=3)
    assert summary.destination_ips.top == [GroupValueCount(value="198.51.100.15", offenses=6)]
    assert summary.log_sources.top == [GroupValueCount(value="112", offenses=6)]
    assert summary.categories.top == [GroupValueCount(value="Firewall Permit", offenses=6)]
    assert summary.usernames.distinct == 1
    # The same records give the same summary.
    assert (await state(groups))[0] == summary
    assert analyzed  # the full analyses are in the count, not among the offenses taken


async def test_grouped_offenses_the_catalog_now_skips_leave_the_group_case(
    sessions: SessionFactory, groups: GroupCaseActivities
) -> None:
    """Criterion 7: an offense the group took whose rules are all `skip` now is `skipped`,
    gets no group note and leaves the summary; with no offense left there is no summary."""
    await make_storm(sessions)
    await catalog_rule(sessions, RULE, mode=CatalogMode.SKIP)

    summary, taken, _ = await state(groups)

    assert (summary, taken) == (None, [])
    for offense_id in unsampled(6)[LIMIT:]:
        row = await seen(sessions, offense_id)
        assert row is not None
        assert (row.status, row.catalog_mode) == (OffenseStatus.SKIPPED, CatalogMode.SKIP)


async def test_an_unknown_group_is_an_error(groups: GroupCaseActivities) -> None:
    with pytest.raises(Exception, match="unknown"):
        await ActivityEnvironment().run(groups.group_case_state, "G-404")


async def test_the_group_enrichment_adds_the_group_catalog_and_floor(
    sessions: SessionFactory, groups: GroupCaseActivities
) -> None:
    await catalog_rule(sessions, RULE, min_level=Level.MEDIUM)
    await catalog_log_source(sessions, 112, "Microsoft Windows Security Event Log")
    _, grouped = await make_storm(sessions)
    summary, _, _ = await state(groups)
    assert summary is not None
    # The example offense as QRadar reports it now, with another log source.
    example = offense(grouped[0], log_source_ids=[999])

    enrichment = await ActivityEnvironment().run(groups.enrich_group, GROUP_ID, example, summary)

    assert enrichment.group_id == GROUP_ID
    assert [rule.rule_id for rule in enrichment.catalog.rules] == [RULE]
    assert [source.log_source_id for source in enrichment.catalog.log_sources] == [112]
    assert enrichment.floor_level is Level.MEDIUM


async def test_the_group_case_opens_once_with_its_sla(
    sessions: SessionFactory, groups: GroupCaseActivities
) -> None:
    """Criterion 1: the case `group-<group_id>` (source `group`) is opened once; the group
    records it. The SLA of a group without a high floor is the long one, from the storm's
    start."""
    await make_storm(sessions)
    env = ActivityEnvironment()
    storm_start = NOW + timedelta(minutes=2)

    async def begin(evaluation_no: int, at: datetime, floor: Level | None = None) -> datetime:
        return await env.run(
            groups.begin_group_evaluation,
            CASE_ID,
            GROUP_ID,
            evaluation_no,
            floor,
            at,
            CASE_ID,
            "run-1",
        )

    due = await begin(1, storm_start)
    assert due == storm_start + SETTINGS.sla_low
    assert await begin(1, storm_start + timedelta(hours=1)) == due  # a retry changes nothing
    stored = await case(sessions, CASE_ID)
    assert stored is not None
    assert (stored.source, stored.group_id, stored.offense_id, stored.evaluation_no) == (
        CaseSource.GROUP,
        GROUP_ID,
        None,
        1,
    )
    stored_group = await group(sessions, GROUP_ID)
    assert stored_group is not None
    assert stored_group.case_id == CASE_ID

    later = storm_start + timedelta(hours=3)
    assert await begin(2, later, Level.HIGH) == later + SETTINGS.sla_high
    stored = await case(sessions, CASE_ID)
    assert stored is not None
    assert stored.evaluation_no == 2


async def test_the_group_closes_only_after_its_window(
    sessions: SessionFactory, groups: GroupCaseActivities
) -> None:
    await make_storm(sessions)
    env = ActivityEnvironment()
    await env.run(groups.begin_group_evaluation, CASE_ID, GROUP_ID, 1, None, NOW, CASE_ID, "run-1")
    window_end = NOW + timedelta(minutes=5, hours=24)

    assert await env.run(groups.close_group_case, GROUP_ID, CASE_ID, window_end) == window_end
    stored_group = await group(sessions, GROUP_ID)
    assert stored_group is not None
    assert stored_group.status is GroupStatus.STORM

    after = window_end + timedelta(seconds=1)
    assert await env.run(groups.close_group_case, GROUP_ID, CASE_ID, after) is None
    stored_group = await group(sessions, GROUP_ID)
    stored = await case(sessions, CASE_ID)
    assert stored_group is not None
    assert stored is not None
    assert (stored_group.status, stored.status) == (GroupStatus.CLOSED, CaseStatus.CLOSED)


async def test_the_settle_time_is_the_setting(groups: GroupCaseActivities) -> None:
    assert await ActivityEnvironment().run(groups.group_settle_delay) == timedelta(minutes=7)
