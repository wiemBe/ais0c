"""Pre-priority: the order in which pending offenses start after a backlog (architecture §9)."""

from ais0c_activities.levels import level_rank
from ais0c_contracts import Level


def pre_priority(*, catalog_floor: Level | None, critical_asset_hit: bool, ioc_hit: bool) -> int:
    """Higher starts first.

    Offenses whose catalog floor is high or critical come first, then those with a critical
    asset or IOC hit, then the rest. Within each tier a higher catalog floor comes first, then an
    offense with a hit.
    """
    hit = critical_asset_hit or ioc_hit
    floor = level_rank(catalog_floor)
    if floor >= level_rank(Level.HIGH):
        tier = 2
    elif hit:
        tier = 1
    else:
        tier = 0
    return tier * 100 + floor * 10 + int(hit)
