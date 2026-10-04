"""When an alert e-mail goes out (architecture §9, "Bildirim seviyesi", "E-posta bildirimi").

Only a notify level of high or critical is e-mailed. When a case is evaluated again, a new
e-mail goes out only if its level is higher than every level already e-mailed about the case:
high after high is not sent again, critical after high is, high after critical is not. Only
e-mails that were sent count; one that was refused, failed or held back by the kill switch does
not.
"""

from collections.abc import Iterable
from typing import Final

from ais0c_contracts import Level

ALERT_LEVELS: Final = frozenset({Level.HIGH, Level.CRITICAL})

# `Level` values compare as strings, not by severity.
_RANK: Final = {Level.LOW: 1, Level.MEDIUM: 2, Level.HIGH: 3, Level.CRITICAL: 4}


def alert_needed(level: Level, sent: Iterable[Level] = ()) -> bool:
    """Whether an evaluation at notify level `level` gets an e-mail, given the levels of the
    e-mails already sent about the same case (`sent`, in any order; empty for the first)."""
    level = Level(level)
    if level not in ALERT_LEVELS:
        return False
    return all(_RANK[level] > _RANK[Level(previous)] for previous in sent)
