"""Severity order of `Level`; the enum's values compare as strings, not by severity."""

from ais0c_contracts import Level

_RANK = {Level.LOW: 1, Level.MEDIUM: 2, Level.HIGH: 3, Level.CRITICAL: 4}


def level_rank(level: Level | None) -> int:
    """0 for no level, then low < medium < high < critical."""
    return 0 if level is None else _RANK[level]


def max_level(*levels: Level | None) -> Level | None:
    """The most severe of `levels`; None when every one is None."""
    present = [level for level in levels if level is not None]
    return max(present, key=level_rank, default=None)


def at_least(level: Level, floor: Level | None) -> Level:
    """`level`, raised to `floor` when the floor is higher."""
    return floor if floor is not None and level_rank(floor) > level_rank(level) else level
