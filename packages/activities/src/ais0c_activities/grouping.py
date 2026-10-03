"""Offense grouping and storm protection (architecture §9, decision T-14).

`decide_grouping` is a pure function. The intake activity reads the group's state from
`offense_groups` and `offenses_seen`, asks for a decision and stores it.

- The group key is the hash of the offense's rule set. Offenses of the same rule set that arrive
  within a 24-hour sliding window share a group: every arrival moves the window's end to 24 hours
  after it.
- At most N offenses of a group per hour get a full analysis. The first offense over the limit
  starts the group evaluation (the group becomes a storm); later ones over the limit are only
  added to the group.
- An offense with a critical asset hit, an IOC hit or a catalog floor of high or critical always
  gets a full analysis, so an attack cannot hide inside a storm.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from ais0c_activities.levels import level_rank
from ais0c_contracts import Level
from ais0c_storage.enums import GroupStatus

GROUP_WINDOW: Final = timedelta(hours=24)
RATE_WINDOW: Final = timedelta(hours=1)


class GroupingOutcome(StrEnum):
    FULL_ANALYSIS = "full_analysis"
    ADD_TO_GROUP = "add_to_group"
    START_GROUP_EVALUATION = "start_group_evaluation"


@dataclass(frozen=True)
class GroupState:
    """A group of the offense's rule set as stored, with its recent full analyses."""

    group_id: str
    rule_set_hash: str
    window_end: datetime
    status: GroupStatus
    # Offenses of the group that went to full analysis in the hour before the new arrival.
    full_analyses_last_hour: int


@dataclass(frozen=True)
class GroupingDecision:
    outcome: GroupingOutcome
    rule_set_hash: str
    # The group the offense joins; a new group's ID when `new_group` is set.
    group_id: str
    new_group: bool
    # The group's window end after this arrival.
    window_end: datetime


def rule_set_hash(rule_ids: Iterable[int]) -> str:
    """The group key: SHA-256 of the offense's distinct rule IDs in ascending order."""
    canonical = ",".join(str(rule_id) for rule_id in sorted(set(rule_ids)))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def new_group_id(key: str, at: datetime) -> str:
    """ID of a group opened at `at`: `G-<key prefix>-<UTC time>`."""
    return f"G-{key[:12]}-{at.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def decide_grouping(
    *,
    rule_ids: Iterable[int],
    at: datetime,
    group: GroupState | None,
    critical_asset_hit: bool,
    ioc_hit: bool,
    catalog_floor: Level | None,
    full_analyses_per_hour: int,
) -> GroupingDecision:
    """Decide what happens to an offense that arrives at `at`.

    `group` is the newest group of the offense's rule set, if there is one; it is ignored when it
    belongs to another rule set, is closed or its window ended before `at`.
    """
    key = rule_set_hash(rule_ids)
    current = (
        group
        if group is not None
        and group.rule_set_hash == key
        and group.status is not GroupStatus.CLOSED
        and at <= group.window_end
        else None
    )
    exempt = critical_asset_hit or ioc_hit or level_rank(catalog_floor) >= level_rank(Level.HIGH)
    recent = 0 if current is None else current.full_analyses_last_hour
    if exempt or recent < full_analyses_per_hour:
        outcome = GroupingOutcome.FULL_ANALYSIS
    elif current is not None and current.status is GroupStatus.STORM:
        outcome = GroupingOutcome.ADD_TO_GROUP
    else:
        outcome = GroupingOutcome.START_GROUP_EVALUATION
    return GroupingDecision(
        outcome=outcome,
        rule_set_hash=key,
        group_id=new_group_id(key, at) if current is None else current.group_id,
        new_group=current is None,
        window_end=at + GROUP_WINDOW,
    )
