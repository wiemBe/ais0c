"""Storm protection in the intake, on a real database (T-027 criteria 4, 5 and 7).

- Every offense that joins a group records its values there; a log source or category new to the
  group lets an offense over the hourly limit escape the group, at most N an hour on a counter of
  its own. A new source IP, destination IP or user does not.
- The hourly sample: at most one offense of a storm an hour goes to a full analysis.
- A pending or grouped offense whose rules are all `skip` now becomes `skipped`; one of its
  rules back on `analyze` admits it again (T-014).

The hourly sample depends on the group's ID, the hour and the offense's ID. Tests that are not
about it use offense IDs the sample does not choose, so the outcome is the rule under test.
IPs are from the RFC 5737 ranges.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from activity_db import catalog_rule, critical_asset, group, seen
from activity_payloads import T0, offense
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CaseSettings,
    FakeOffenseSource,
    IntakeActivities,
    SessionFactory,
    rule_set_hash,
    sample_chosen,
)
from ais0c_activities.grouping import new_group_id
from ais0c_contracts import CatalogMode, OffenseSnapshot
from ais0c_storage.enums import (
    CriticalAssetKind,
    FullAnalysisReason,
    GroupStatus,
    GroupValueKind,
    OffenseStatus,
)
from ais0c_storage.repositories import GroupValueCounts, count_group_values

pytestmark = pytest.mark.anyio

NOW = T0 + timedelta(minutes=5)
RULE = 100201
LIMIT = 5
GROUP_ID = new_group_id(rule_set_hash([RULE]), NOW)
ALL_STATUSES = frozenset(OffenseStatus)


@pytest.fixture
def intake(sessions: SessionFactory) -> IntakeActivities:
    return IntakeActivities(
        sessions=sessions,
        source=FakeOffenseSource(),
        settings=CaseSettings(
            case_url_base="https://ais0c.example.com/cases", group_full_analyses_per_hour=LIMIT
        ),
    )


def unsampled(start: int, at: datetime = NOW) -> Iterator[int]:
    """Offense IDs from `start` on that are not the group's sample in the hour of `at`."""
    number = start
    while True:
        if not sample_chosen(GROUP_ID, at, number):
            yield number
        number += 1


def sampled(start: int, at: datetime = NOW) -> int:
    """The first offense ID from `start` on that the sample may choose in the hour of `at`."""
    number = start
    while not sample_chosen(GROUP_ID, at, number):
        number += 1
    return number


async def admit(intake: IntakeActivities, *offenses: OffenseSnapshot, at: datetime = NOW) -> None:
    for item in offenses:
        await ActivityEnvironment().run(intake.admit_offenses, [item], at)


async def status_and_reason(
    sessions: SessionFactory, offense_id: int
) -> tuple[OffenseStatus, FullAnalysisReason | None]:
    row = await seen(sessions, offense_id)
    assert row is not None
    return row.status, row.full_analysis_reason


async def storm(intake: IntakeActivities, ids: Iterator[int]) -> list[int]:
    """Fill the hour's limit and start the storm: the first N offenses get a full analysis, the
    next one starts the group's evaluation. Every one has the same log source and category."""
    started = [next(ids) for _ in range(LIMIT + 1)]
    await admit(intake, *(offense(n, source_ips=[f"203.0.113.{n % 250}"]) for n in started))
    return started


async def values(sessions: SessionFactory) -> dict[GroupValueKind, GroupValueCounts]:
    async with sessions() as session:
        return await count_group_values(session, GROUP_ID, top=10, statuses=ALL_STATUSES)


async def test_the_limit_then_the_storm(sessions: SessionFactory, intake: IntakeActivities) -> None:
    ids = unsampled(100)
    started = await storm(intake, ids)

    reasons = [await status_and_reason(sessions, n) for n in started]
    assert reasons == [(OffenseStatus.PENDING, FullAnalysisReason.LIMIT)] * LIMIT + [
        (OffenseStatus.GROUPED, None)
    ]
    stored = await group(sessions, GROUP_ID)
    assert stored is not None
    assert (stored.status, stored.offense_count) == (GroupStatus.STORM, LIMIT + 1)


async def test_every_offense_keeps_its_values_in_its_group(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    await admit(
        intake,
        offense(
            1,
            source_ips=["203.0.113.7", "203.0.113.8"],
            destination_ips=["198.51.100.15"],
            usernames=["svc_backup_7731"],
            log_source_ids=[112, 113],
            categories=["Firewall Permit"],
        ),
        offense(2, source_ips=["203.0.113.7"], usernames=["x" * 400]),
    )

    counted = await values(sessions)
    assert counted[GroupValueKind.SOURCE_IP] == GroupValueCounts(
        distinct=2, top=[("203.0.113.7", 2), ("203.0.113.8", 1)]
    )
    assert counted[GroupValueKind.LOG_SOURCE] == GroupValueCounts(
        distinct=2, top=[("112", 2), ("113", 1)]
    )
    assert counted[GroupValueKind.CATEGORY].top == [("Firewall Permit", 2)]
    # A value from QRadar is cut to the summary's length.
    assert {value for value, _ in counted[GroupValueKind.USERNAME].top} == {
        "svc_backup_7731",
        "x" * 255,
    }


@pytest.mark.parametrize(
    ("kind", "first", "second"),
    [
        ("log source", {"log_source_ids": [113]}, {"log_source_ids": [113]}),
        ("category", {"categories": ["Port Scan"]}, {"categories": ["Port Scan"]}),
    ],
)
async def test_a_new_log_source_or_category_escapes_the_first_time_only(
    sessions: SessionFactory,
    intake: IntakeActivities,
    kind: str,
    first: dict[str, list[int] | list[str]],
    second: dict[str, list[int] | list[str]],
) -> None:
    """Criterion 4: the first sighting in the group escapes, the second joins the group."""
    ids = unsampled(200)
    await storm(intake, ids)
    escapes, joins = next(ids), next(ids)

    await admit(intake, offense(escapes, **first))  # pyright: ignore[reportArgumentType]
    await admit(intake, offense(joins, **second))  # pyright: ignore[reportArgumentType]

    assert await status_and_reason(sessions, escapes) == (
        OffenseStatus.PENDING,
        FullAnalysisReason.NOVELTY,
    ), kind
    assert await status_and_reason(sessions, joins) == (OffenseStatus.GROUPED, None), kind


async def test_novelty_escapes_stop_at_their_hourly_limit(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    ids = unsampled(300)
    await storm(intake, ids)
    novel = [next(ids) for _ in range(LIMIT + 2)]

    await admit(intake, *(offense(n, log_source_ids=[1000 + n]) for n in novel))

    reasons = [await status_and_reason(sessions, n) for n in novel]
    assert (
        reasons
        == [(OffenseStatus.PENDING, FullAnalysisReason.NOVELTY)] * LIMIT
        + [(OffenseStatus.GROUPED, None)] * 2
    )
    # The next hour has room again.
    later = NOW + timedelta(minutes=61)
    fresh = next(unsampled(400, later))
    await admit(intake, offense(fresh, log_source_ids=[2000]), at=later)
    assert (await status_and_reason(sessions, fresh))[1] in (
        FullAnalysisReason.LIMIT,
        FullAnalysisReason.NOVELTY,
    )


async def test_a_new_source_destination_or_user_does_not_escape_but_is_counted(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    """T-62: in a storm nearly every offense carries a new source, destination or user."""
    ids = unsampled(500)
    await storm(intake, ids)
    new_source, new_destination, new_user = next(ids), next(ids), next(ids)

    await admit(
        intake,
        offense(new_source, source_ips=["192.0.2.201"]),
        offense(new_destination, destination_ips=["192.0.2.202"]),
        offense(new_user, usernames=["svc_newuser_0042"]),
    )

    for offense_id in (new_source, new_destination, new_user):
        assert await status_and_reason(sessions, offense_id) == (OffenseStatus.GROUPED, None)
    counted = await values(sessions)
    assert ("192.0.2.201", 1) in counted[GroupValueKind.SOURCE_IP].top
    assert ("192.0.2.202", 1) in counted[GroupValueKind.DESTINATION_IP].top
    assert counted[GroupValueKind.USERNAME].top == [("svc_newuser_0042", 1)]


@pytest.mark.parametrize("new_log_sources", [False, True], ids=["one log source", "each new"])
async def test_fifty_sources_in_an_hour_stay_within_the_limits(
    sessions: SessionFactory, intake: IntakeActivities, new_log_sources: bool
) -> None:
    """Negative: a spray of 50 offenses with 50 source IPs in one hour gets at most the hourly
    limit, the sample and the novelty limit as full analyses, even when every offense also
    brings a new log source. Offenses with a critical asset escape without a limit."""
    await critical_asset(sessions, CriticalAssetKind.IP, "192.0.2.50", "SWIFT")
    spray = [
        offense(
            700 + n,
            source_ips=[f"203.0.113.{n}"],
            log_source_ids=[3000 + n] if new_log_sources else [112],
        )
        for n in range(50)
    ]
    swift = [offense(800 + n, destination_ips=["192.0.2.50"]) for n in range(8)]

    for minute, item in enumerate([*spray, *swift]):
        await admit(intake, item, at=NOW + timedelta(seconds=minute * 60))

    rows = [await seen(sessions, item.offense_id) for item in spray]
    reasons = [row.full_analysis_reason for row in rows if row is not None]
    full = [reason for reason in reasons if reason is not None]
    assert reasons.count(FullAnalysisReason.SAMPLE) <= 1
    assert reasons.count(FullAnalysisReason.LIMIT) == LIMIT
    assert reasons.count(FullAnalysisReason.NOVELTY) == (LIMIT if new_log_sources else 0)
    assert len(full) <= LIMIT + 1 + (LIMIT if new_log_sources else 0)
    grouped = [row for row in rows if row is not None and row.status is OffenseStatus.GROUPED]
    assert len(grouped) == 50 - len(full)
    for item in swift:
        assert await status_and_reason(sessions, item.offense_id) == (
            OffenseStatus.PENDING,
            FullAnalysisReason.EXEMPT,
        )


async def test_a_storm_hour_has_at_most_one_sample(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    """Criterion 5: an offense the hash chooses goes to a full analysis; after it, the hour has
    no other sample, not even one the hash would choose."""
    ids = unsampled(900)
    await storm(intake, ids)
    first = sampled(950)
    second = sampled(first + 1)

    await admit(intake, offense(first), offense(second))

    assert await status_and_reason(sessions, first) == (
        OffenseStatus.PENDING,
        FullAnalysisReason.SAMPLE,
    )
    assert await status_and_reason(sessions, second) == (OffenseStatus.GROUPED, None)


async def test_a_pending_offense_the_catalog_now_skips_does_not_start(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    """Criterion 7: the catalog is checked once more before a pending offense's case starts."""
    await admit(intake, offense(60), offense(61, rule_ids=[100305]))
    await catalog_rule(sessions, RULE, mode=CatalogMode.SKIP)

    assert await ActivityEnvironment().run(intake.next_pending_offenses) == [61]

    assert await status_and_reason(sessions, 60) == (
        OffenseStatus.SKIPPED,
        FullAnalysisReason.LIMIT,
    )
    row = await seen(sessions, 60)
    assert row is not None
    assert row.catalog_mode is CatalogMode.SKIP


async def test_a_grouped_offense_the_catalog_now_skips_is_skipped_then_admitted_again(
    sessions: SessionFactory, intake: IntakeActivities
) -> None:
    """Criterion 7: an update of a grouped offense whose rules are all `skip` now makes it
    `skipped`; once a rule is `analyze` again, its next update admits it like a new offense,
    back into its group without counting it twice."""
    ids = unsampled(1000)
    started = await storm(intake, ids)
    grouped = started[-1]
    await catalog_rule(sessions, RULE, mode=CatalogMode.SKIP)

    v2 = T0 + timedelta(minutes=2)
    await admit(intake, offense(grouped, updated=v2), at=NOW + timedelta(minutes=1))
    assert (await status_and_reason(sessions, grouped))[0] is OffenseStatus.SKIPPED

    await catalog_rule(sessions, RULE, mode=CatalogMode.ANALYZE)
    v3 = T0 + timedelta(minutes=3)
    await admit(intake, offense(grouped, updated=v3), at=NOW + timedelta(minutes=2))

    row = await seen(sessions, grouped)
    assert row is not None
    assert row.group_id == GROUP_ID
    assert row.status is OffenseStatus.GROUPED
    stored = await group(sessions, GROUP_ID)
    assert stored is not None
    assert stored.offense_count == LIMIT + 1
