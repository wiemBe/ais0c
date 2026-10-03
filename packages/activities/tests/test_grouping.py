"""`decide_grouping`: one test group per rule (criterion 4)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ais0c_activities import (
    GROUP_WINDOW,
    GroupingOutcome,
    GroupState,
    decide_grouping,
    rule_set_hash,
)
from ais0c_activities.grouping import new_group_id
from ais0c_contracts import Level
from ais0c_storage.enums import GroupStatus

NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
RULES = (100201, 100305)
LIMIT = 5


def group(
    *, recent: int = 0, status: GroupStatus = GroupStatus.OPEN, window_end: datetime | None = None
) -> GroupState:
    return GroupState(
        group_id="G-existing",
        rule_set_hash=rule_set_hash(RULES),
        window_end=NOW + timedelta(hours=1) if window_end is None else window_end,
        status=status,
        full_analyses_last_hour=recent,
    )


def decide(
    state: GroupState | None,
    *,
    rule_ids: tuple[int, ...] = RULES,
    critical_asset_hit: bool = False,
    ioc_hit: bool = False,
    catalog_floor: Level | None = None,
) -> GroupingOutcome:
    return decide_grouping(
        rule_ids=rule_ids,
        at=NOW,
        group=state,
        critical_asset_hit=critical_asset_hit,
        ioc_hit=ioc_hit,
        catalog_floor=catalog_floor,
        full_analyses_per_hour=LIMIT,
    ).outcome


# --- The group key is the hash of the rule set; the window is 24 hours -----------------------


def test_the_group_key_is_the_hash_of_the_rule_set() -> None:
    assert rule_set_hash([100305, 100201, 100201]) == rule_set_hash([100201, 100305])
    assert rule_set_hash([100201]) != rule_set_hash([100201, 100305])
    assert len(rule_set_hash(RULES)) == 64


def test_an_offense_joins_the_open_group_of_its_rule_set() -> None:
    decision = decide_grouping(
        rule_ids=(100305, 100201),
        at=NOW,
        group=group(),
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        full_analyses_per_hour=LIMIT,
    )

    assert (decision.group_id, decision.new_group) == ("G-existing", False)
    assert decision.rule_set_hash == rule_set_hash(RULES)


@pytest.mark.parametrize(
    "stale",
    [
        replace(group(), rule_set_hash=rule_set_hash([999])),
        group(window_end=NOW - timedelta(seconds=1)),
        group(status=GroupStatus.CLOSED),
    ],
    ids=["other rule set", "window ended", "closed"],
)
def test_a_new_group_opens_when_there_is_no_current_one(stale: GroupState) -> None:
    decision = decide_grouping(
        rule_ids=RULES,
        at=NOW,
        group=stale,
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        full_analyses_per_hour=LIMIT,
    )

    assert decision.new_group is True
    assert decision.group_id == new_group_id(rule_set_hash(RULES), NOW)
    assert decision.outcome is GroupingOutcome.FULL_ANALYSIS


def test_the_window_slides_to_24_hours_after_each_arrival() -> None:
    assert GROUP_WINDOW == timedelta(hours=24)
    at_the_edge = group(window_end=NOW)

    decision = decide_grouping(
        rule_ids=RULES,
        at=NOW,
        group=at_the_edge,
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        full_analyses_per_hour=LIMIT,
    )

    assert decision.new_group is False
    assert decision.window_end == NOW + timedelta(hours=24)


def test_new_group_ids_are_readable_and_utc() -> None:
    key = rule_set_hash(RULES)
    local = NOW.astimezone(ZoneInfo("Europe/Istanbul"))

    assert new_group_id(key, local) == f"G-{key[:12]}-20261002T100000Z"


# --- At most N full analyses per group and hour ----------------------------------------------


@pytest.mark.parametrize("recent", [0, LIMIT - 1])
def test_under_the_hourly_limit_an_offense_gets_a_full_analysis(recent: int) -> None:
    assert decide(group(recent=recent)) is GroupingOutcome.FULL_ANALYSIS


def test_the_first_offense_over_the_limit_starts_the_group_evaluation() -> None:
    assert decide(group(recent=LIMIT)) is GroupingOutcome.START_GROUP_EVALUATION


def test_later_offenses_over_the_limit_are_added_to_the_storm_group() -> None:
    assert decide(group(recent=LIMIT + 3, status=GroupStatus.STORM)) is GroupingOutcome.ADD_TO_GROUP


def test_a_storm_group_under_the_limit_again_gets_full_analyses() -> None:
    assert decide(group(recent=1, status=GroupStatus.STORM)) is GroupingOutcome.FULL_ANALYSIS


def test_the_limit_is_a_parameter() -> None:
    decision = decide_grouping(
        rule_ids=RULES,
        at=NOW,
        group=group(recent=2),
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        full_analyses_per_hour=2,
    )
    assert decision.outcome is GroupingOutcome.START_GROUP_EVALUATION


# --- Critical asset, IOC and high/critical catalog floor always get a full analysis ----------


@pytest.mark.parametrize(
    ("critical_asset_hit", "ioc_hit", "catalog_floor"),
    [
        (True, False, None),
        (False, True, None),
        (False, False, Level.HIGH),
        (False, False, Level.CRITICAL),
    ],
    ids=["critical asset", "IOC", "floor high", "floor critical"],
)
@pytest.mark.parametrize(
    "state",
    [group(recent=LIMIT), group(recent=50, status=GroupStatus.STORM)],
    ids=["limit reached", "storm"],
)
def test_exempt_offenses_always_get_a_full_analysis(
    critical_asset_hit: bool, ioc_hit: bool, catalog_floor: Level | None, state: GroupState
) -> None:
    outcome = decide(
        state, critical_asset_hit=critical_asset_hit, ioc_hit=ioc_hit, catalog_floor=catalog_floor
    )
    assert outcome is GroupingOutcome.FULL_ANALYSIS


@pytest.mark.parametrize("floor", [Level.LOW, Level.MEDIUM])
def test_a_lower_catalog_floor_is_not_exempt(floor: Level) -> None:
    state = group(recent=LIMIT, status=GroupStatus.STORM)
    assert decide(state, catalog_floor=floor) is GroupingOutcome.ADD_TO_GROUP
