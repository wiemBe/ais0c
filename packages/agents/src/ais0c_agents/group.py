"""The summary of an offense group: Triage's input in a group case (architecture §9, "Offense
gruplama ve fırtına koruması"; T-14, T-62).

A group whose offenses went over their hourly full analysis limit is evaluated as one case. The
platform counts what the group's offenses carry, deterministically, from its own records; Triage
gets the counts beside the snapshot of one offense of the group. Every value comes from QRadar,
so the summary reaches the model only inside the `untrusted_*` wrapper, with the source
`qradar.group_summary` (docs/impl/prompts.md).

The group's ID stays out, as the enrichment's does: it is the platform's own bookkeeping.
"""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ais0c_contracts import UtcDatetime

GROUP_SUMMARY_SOURCE: Final = "qradar.group_summary"
# The most frequent values of each kind the summary lists, and the rules it names.
MAX_GROUP_TOP_VALUES: Final = 10
MAX_GROUP_RULES: Final = 50
# A value or a rule name longer than this is cut; it comes from QRadar and may be anything.
MAX_GROUP_VALUE_LENGTH: Final = 255


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroupValueCount(_Model):
    """A value and the number of the group's offenses that carry it."""

    value: Annotated[str, StringConstraints(max_length=MAX_GROUP_VALUE_LENGTH)]
    offenses: Annotated[int, Field(ge=1)]


class GroupValues(_Model):
    """One kind of value: how many different ones the group's offenses carry, and the most
    frequent first (equal counts in text order)."""

    distinct: Annotated[int, Field(ge=0)]
    top: Annotated[list[GroupValueCount], Field(max_length=MAX_GROUP_TOP_VALUES)]


class GroupRule(_Model):
    rule_id: int
    # The rule's name as the Analysis Catalog synced it from QRadar; None when it has none.
    name: Annotated[str, StringConstraints(max_length=MAX_GROUP_VALUE_LENGTH)] | None


class GroupSummary(_Model):
    """The group's offenses so far, counted; Triage's `group_summary`."""

    offense_count: Annotated[int, Field(ge=1)]
    # When the platform recorded the group's first and its latest offense.
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime
    # The offense whose snapshot Triage gets beside the summary.
    example_offense_id: int
    rules: Annotated[list[GroupRule], Field(max_length=MAX_GROUP_RULES)]
    source_ips: GroupValues
    destination_ips: GroupValues
    usernames: GroupValues
    log_sources: GroupValues
    """QRadar log source IDs, as text."""
    categories: GroupValues
