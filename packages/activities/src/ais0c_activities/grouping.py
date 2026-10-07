"""Offense grouping and storm protection (architecture §9; decisions T-14, T-22, T-46, T-62).

`decide_grouping` is a pure function. The intake activity reads the group's state from
`offense_groups`, `offenses_seen` and `offense_group_values`, asks for a decision and stores it.

- The group key is the hash of the offense's rule set. Offenses of the same rule set that arrive
  within a 24-hour sliding window share a group: every arrival moves the window's end to 24 hours
  after it.
- At most N offenses of a group per hour get a full analysis: the limit. The first offense over
  the limit that the group takes starts the group evaluation (the group becomes a storm); later
  ones over the limit are only added to the group.
- An offense with a critical asset hit (a privileged user is one), an IOC hit or a catalog floor
  of high or critical always gets a full analysis, so an attack cannot hide inside a storm. It
  counts against the limit like the offenses within it.
- An offense over the limit that carries a log source or offense category the group has not seen
  gets a full analysis too: a novelty escape (T-46). A group has at most N of them per hour, on a
  counter of its own; more go into the group. A source IP, destination IP or user the group has
  not seen is no escape (T-62): in a storm nearly every offense carries one, because QRadar adds
  the events of a known source to its open offense. Those values go into the group's summary.
- In a storm, at most one offense per hour that the group would take gets a full analysis
  instead: the hourly sample (T-22). `sample_chosen` picks it from a hash of the group, the hour
  and the offense, so a replay picks the same and an outsider, who knows neither the group's ID
  nor the next offense IDs, cannot tell which.

Every full analysis carries its reason (`FullAnalysisReason`); the intake counts the reasons of
the group's offenses in the hour before an arrival.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from ais0c_activities.levels import level_rank
from ais0c_contracts import Level
from ais0c_storage.enums import FullAnalysisReason, GroupStatus

GROUP_WINDOW: Final = timedelta(hours=24)
RATE_WINDOW: Final = timedelta(hours=1)
# One offense in this many that a storm group would take is its hour's sample, until the hour
# has one: a group that takes 5 offenses an hour is sampled in about 3 hours of 4, one that
# takes 20 in nearly every hour.
SAMPLE_ONE_IN: Final = 4


class GroupingOutcome(StrEnum):
    FULL_ANALYSIS = "full_analysis"
    ADD_TO_GROUP = "add_to_group"
    START_GROUP_EVALUATION = "start_group_evaluation"


@dataclass(frozen=True)
class GroupState:
    """A group of the offense's rule set as stored, with its counters of the hour before the
    new arrival."""

    group_id: str
    rule_set_hash: str
    window_end: datetime
    status: GroupStatus
    # Offenses of the group that went to full analysis within the limit or as exempt.
    full_analyses_last_hour: int
    # Offenses of the group that went to full analysis as novelty escapes.
    novelty_escapes_last_hour: int = 0
    # Whether an offense of the group was the hourly sample.
    sampled_last_hour: bool = False


@dataclass(frozen=True)
class GroupingDecision:
    outcome: GroupingOutcome
    rule_set_hash: str
    # The group the offense joins; a new group's ID when `new_group` is set.
    group_id: str
    new_group: bool
    # The group's window end after this arrival.
    window_end: datetime
    # Why the offense gets a full analysis; None when the group takes it.
    reason: FullAnalysisReason | None = None


def rule_set_hash(rule_ids: Iterable[int]) -> str:
    """The group key: SHA-256 of the offense's distinct rule IDs in ascending order."""
    canonical = ",".join(str(rule_id) for rule_id in sorted(set(rule_ids)))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def new_group_id(key: str, at: datetime) -> str:
    """ID of a group opened at `at`: `G-<key prefix>-<UTC time>`."""
    return f"G-{key[:12]}-{at.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def sample_hour(at: datetime) -> datetime:
    """The UTC hour `at` falls in, the hour of the sample's hash."""
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def sample_value(group_id: str, at: datetime, offense_id: int) -> int:
    """The first 8 bytes of sha256("<group_id>:<UTC hour>:<offense_id>") as a number."""
    key = f"{group_id}:{sample_hour(at):%Y-%m-%dT%H}:{offense_id}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")


def sample_chosen(group_id: str, at: datetime, offense_id: int) -> bool:
    """Whether the offense arriving at `at` may be its group's hourly sample: one in
    SAMPLE_ONE_IN, by `sample_value`. The caller allows one sample per hour."""
    return sample_value(group_id, at, offense_id) % SAMPLE_ONE_IN == 0


def decide_grouping(
    *,
    offense_id: int,
    rule_ids: Iterable[int],
    at: datetime,
    group: GroupState | None,
    critical_asset_hit: bool,
    ioc_hit: bool,
    catalog_floor: Level | None,
    novel: bool,
    full_analyses_per_hour: int,
) -> GroupingDecision:
    """Decide what happens to an offense that arrives at `at`.

    `group` is the newest group of the offense's rule set, if there is one; it is ignored when it
    belongs to another rule set, is closed or its window ended before `at`. `novel` says the
    offense carries a log source or offense category that group has not seen.
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
    reason = _full_analysis_reason(
        current,
        offense_id=offense_id,
        at=at,
        exempt=exempt,
        novel=novel,
        per_hour=full_analyses_per_hour,
    )
    if reason is not None:
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
        reason=reason,
    )


def _full_analysis_reason(
    current: GroupState | None,
    *,
    offense_id: int,
    at: datetime,
    exempt: bool,
    novel: bool,
    per_hour: int,
) -> FullAnalysisReason | None:
    """Why the offense gets a full analysis, in this order; None when the group takes it."""
    if exempt:
        return FullAnalysisReason.EXEMPT
    if current is None or current.full_analyses_last_hour < per_hour:
        return FullAnalysisReason.LIMIT
    if novel and current.novelty_escapes_last_hour < per_hour:
        return FullAnalysisReason.NOVELTY
    if (
        current.status is GroupStatus.STORM
        and not current.sampled_last_hour
        and sample_chosen(current.group_id, at, offense_id)
    ):
        return FullAnalysisReason.SAMPLE
    return None
