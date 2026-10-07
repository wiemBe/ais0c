"""`decide_grouping`: one test group per rule (T-010 criterion 4; T-027 criteria 4 and 5)."""

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
from ais0c_activities.grouping import (
    SAMPLE_ONE_IN,
    new_group_id,
    sample_chosen,
    sample_hour,
    sample_value,
)
from ais0c_contracts import Level
from ais0c_storage.enums import FullAnalysisReason, GroupStatus

NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
RULES = (100201, 100305)
LIMIT = 5
GROUP_ID = "G-existing"
# Offense IDs the hash makes this hour's sample of the group, and ones it does not.
SAMPLED = next(n for n in range(1, 1000) if sample_chosen(GROUP_ID, NOW, n))
NOT_SAMPLED = next(n for n in range(1, 1000) if not sample_chosen(GROUP_ID, NOW, n))


def group(
    *,
    recent: int = 0,
    status: GroupStatus = GroupStatus.OPEN,
    window_end: datetime | None = None,
    novelty: int = 0,
    sampled: bool = False,
) -> GroupState:
    return GroupState(
        group_id=GROUP_ID,
        rule_set_hash=rule_set_hash(RULES),
        window_end=NOW + timedelta(hours=1) if window_end is None else window_end,
        status=status,
        full_analyses_last_hour=recent,
        novelty_escapes_last_hour=novelty,
        sampled_last_hour=sampled,
    )


def decide(
    state: GroupState | None,
    *,
    rule_ids: tuple[int, ...] = RULES,
    critical_asset_hit: bool = False,
    ioc_hit: bool = False,
    catalog_floor: Level | None = None,
    novel: bool = False,
    offense_id: int = NOT_SAMPLED,
) -> GroupingOutcome:
    return decide_grouping(
        offense_id=offense_id,
        rule_ids=rule_ids,
        at=NOW,
        group=state,
        critical_asset_hit=critical_asset_hit,
        ioc_hit=ioc_hit,
        catalog_floor=catalog_floor,
        novel=novel,
        full_analyses_per_hour=LIMIT,
    ).outcome


# --- The group key is the hash of the rule set; the window is 24 hours -----------------------


def test_the_group_key_is_the_hash_of_the_rule_set() -> None:
    assert rule_set_hash([100305, 100201, 100201]) == rule_set_hash([100201, 100305])
    assert rule_set_hash([100201]) != rule_set_hash([100201, 100305])
    assert len(rule_set_hash(RULES)) == 64


def test_an_offense_joins_the_open_group_of_its_rule_set() -> None:
    decision = decide_grouping(
        offense_id=NOT_SAMPLED,
        rule_ids=(100305, 100201),
        at=NOW,
        group=group(),
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        novel=False,
        full_analyses_per_hour=LIMIT,
    )

    assert (decision.group_id, decision.new_group) == (GROUP_ID, False)
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
        offense_id=NOT_SAMPLED,
        rule_ids=RULES,
        at=NOW,
        group=stale,
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        novel=False,
        full_analyses_per_hour=LIMIT,
    )

    assert decision.new_group is True
    assert decision.group_id == new_group_id(rule_set_hash(RULES), NOW)
    assert decision.outcome is GroupingOutcome.FULL_ANALYSIS


def test_the_window_slides_to_24_hours_after_each_arrival() -> None:
    assert GROUP_WINDOW == timedelta(hours=24)
    at_the_edge = group(window_end=NOW)

    decision = decide_grouping(
        offense_id=NOT_SAMPLED,
        rule_ids=RULES,
        at=NOW,
        group=at_the_edge,
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        novel=False,
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
        offense_id=NOT_SAMPLED,
        rule_ids=RULES,
        at=NOW,
        group=group(recent=2),
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        novel=False,
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


def test_exempt_and_limited_full_analyses_carry_their_reason() -> None:
    def reason(**fields: object) -> FullAnalysisReason | None:
        return decide_grouping(
            offense_id=NOT_SAMPLED,
            rule_ids=RULES,
            at=NOW,
            group=group(recent=LIMIT, status=GroupStatus.STORM),
            critical_asset_hit=bool(fields.get("asset", False)),
            ioc_hit=False,
            catalog_floor=None,
            novel=bool(fields.get("novel", False)),
            full_analyses_per_hour=int(str(fields.get("limit", LIMIT))),
        ).reason

    assert reason(asset=True) is FullAnalysisReason.EXEMPT
    assert reason(limit=LIMIT + 1) is FullAnalysisReason.LIMIT
    assert reason(novel=True) is FullAnalysisReason.NOVELTY
    assert reason() is None


# --- T-027 criterion 4: a log source or category new to the group escapes, N per hour --------


@pytest.mark.parametrize("status", [GroupStatus.OPEN, GroupStatus.STORM])
def test_a_new_log_source_or_category_over_the_limit_gets_a_full_analysis(
    status: GroupStatus,
) -> None:
    """Over the limit, an offense with a value new to the group escapes; it does not start
    the storm, the next offense the group takes does."""
    assert decide(group(recent=LIMIT, status=status), novel=True) is GroupingOutcome.FULL_ANALYSIS


def test_novelty_escapes_stop_at_their_own_hourly_limit() -> None:
    full = group(recent=LIMIT, status=GroupStatus.STORM, novelty=LIMIT)
    almost = group(recent=LIMIT, status=GroupStatus.STORM, novelty=LIMIT - 1)

    assert decide(full, novel=True) is GroupingOutcome.ADD_TO_GROUP
    assert decide(almost, novel=True) is GroupingOutcome.FULL_ANALYSIS


def test_novelty_escapes_do_not_use_up_the_hourly_limit() -> None:
    """A separate counter: the limit's offenses still get theirs when novelties came first."""
    state = group(recent=LIMIT - 1, status=GroupStatus.STORM, novelty=LIMIT)
    assert decide(state) is GroupingOutcome.FULL_ANALYSIS


def test_nothing_new_over_the_limit_joins_the_storm() -> None:
    assert decide(group(recent=LIMIT, status=GroupStatus.STORM)) is GroupingOutcome.ADD_TO_GROUP


# --- T-027 criterion 5: the hourly sample ----------------------------------------------------


def test_the_hourly_sample_of_a_storm_gets_a_full_analysis() -> None:
    decision = decide_grouping(
        offense_id=SAMPLED,
        rule_ids=RULES,
        at=NOW,
        group=group(recent=LIMIT, status=GroupStatus.STORM),
        critical_asset_hit=False,
        ioc_hit=False,
        catalog_floor=None,
        novel=False,
        full_analyses_per_hour=LIMIT,
    )

    assert decision.outcome is GroupingOutcome.FULL_ANALYSIS
    assert decision.reason is FullAnalysisReason.SAMPLE


def test_an_hour_has_at_most_one_sample() -> None:
    state = group(recent=LIMIT, status=GroupStatus.STORM, sampled=True)
    assert decide(state, offense_id=SAMPLED) is GroupingOutcome.ADD_TO_GROUP


def test_an_offense_the_hash_does_not_choose_joins_the_storm() -> None:
    state = group(recent=LIMIT, status=GroupStatus.STORM)
    assert decide(state, offense_id=NOT_SAMPLED) is GroupingOutcome.ADD_TO_GROUP


def test_only_a_storm_is_sampled() -> None:
    """The offense that starts the storm is the group's: the sample is of a storm's hours."""
    state = group(recent=LIMIT, status=GroupStatus.OPEN)
    assert decide(state, offense_id=SAMPLED) is GroupingOutcome.START_GROUP_EVALUATION


def test_the_sample_is_deterministic_and_depends_on_group_hour_and_offense() -> None:
    later = NOW + timedelta(minutes=59)
    next_hour = NOW + timedelta(hours=1)

    assert sample_value(GROUP_ID, NOW, 7) == sample_value(GROUP_ID, later, 7)
    assert sample_hour(later) == NOW
    assert len({sample_value(GROUP_ID, NOW, n) for n in range(50)}) == 50
    assert sample_value(GROUP_ID, NOW, 7) != sample_value(GROUP_ID, next_hour, 7)
    assert sample_value(GROUP_ID, NOW, 7) != sample_value("G-other", NOW, 7)
    # About one in SAMPLE_ONE_IN offenses is a candidate.
    chosen = sum(sample_chosen(GROUP_ID, NOW, n) for n in range(4000))
    assert 4000 / SAMPLE_ONE_IN * 0.8 < chosen < 4000 / SAMPLE_ONE_IN * 1.2
