"""The values a group keeps of its offenses (architecture §9; T-46, T-62).

Every offense that joins a group records what it carries there: its source IPs, destination
IPs, user names, log sources and offense categories (`offense_group_values`). Log sources are
kept as their QRadar IDs, in text. A value longer than MAX_GROUP_VALUE_LENGTH is cut: it comes from
QRadar, may be anything, and the summary shows no more of it.

A log source or category no offense of the group carried before makes an offense over the
hourly limit a novelty escape; the other kinds only go into the group's summary (T-62).
"""

from collections.abc import Mapping, Sequence
from typing import Final

from ais0c_agents import MAX_GROUP_VALUE_LENGTH
from ais0c_contracts import OffenseSnapshot
from ais0c_storage.enums import GroupValueKind

# The kinds whose first sighting in a group is a novelty escape (T-46, T-62).
NOVELTY_KINDS: Final = frozenset({GroupValueKind.LOG_SOURCE, GroupValueKind.CATEGORY})


def offense_values(offense: OffenseSnapshot) -> dict[GroupValueKind, list[str]]:
    """The values of the offense a group keeps, by kind, each once and cut."""
    raw: dict[GroupValueKind, Sequence[str]] = {
        GroupValueKind.SOURCE_IP: offense.source_ips,
        GroupValueKind.DESTINATION_IP: offense.destination_ips,
        GroupValueKind.USERNAME: offense.usernames,
        GroupValueKind.LOG_SOURCE: [str(log_source) for log_source in offense.log_source_ids],
        GroupValueKind.CATEGORY: offense.categories,
    }
    return {
        kind: list(dict.fromkeys(value[:MAX_GROUP_VALUE_LENGTH] for value in values if value))
        for kind, values in raw.items()
    }


def novelty_values(offense: OffenseSnapshot) -> dict[GroupValueKind, list[str]]:
    """The offense's log sources and categories, the values a novelty escape looks at."""
    return {
        kind: values for kind, values in offense_values(offense).items() if kind in NOVELTY_KINDS
    }


def unseen(
    values: Mapping[GroupValueKind, Sequence[str]], seen: set[tuple[GroupValueKind, str]]
) -> list[tuple[GroupValueKind, str]]:
    """The (kind, value) pairs of `values` that are not in `seen`, in their order."""
    return [
        (kind, value)
        for kind, items in values.items()
        for value in items
        if (kind, value) not in seen
    ]
