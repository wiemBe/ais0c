"""When an updated offense is evaluated again (decision D-31, architecture §6).

QRadar moves an offense's `last_updated_time` with every new event, so evaluating every update
would run the Triage agent on every intake pass for a busy offense. An update is evaluated again
only when it brings something the last evaluation did not see:

- a new rule,
- a new source IP, destination IP or user name,
- a new log source,

or, when it only brings more events, once at least the configured interval (30 minutes by
default) has passed since the last evaluation. Anything else, such as values that disappeared or
a new magnitude, is not evaluated again.

The update is compared with the snapshot the last evaluation used, not with the previous update:
what counts is what the AI has not seen yet. User names compare without case, as the critical
asset match does (Windows and AD names are case-insensitive).
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ais0c_contracts import OffenseSnapshot


def should_reevaluate(
    previous: OffenseSnapshot,
    current: OffenseSnapshot,
    *,
    last_evaluated_at: datetime,
    now: datetime,
    min_interval: timedelta,
) -> bool:
    """Whether `current` is evaluated again after an evaluation of `previous` at
    `last_evaluated_at`; `min_interval` is the configured wait for an update that only brings
    more events."""
    if (
        _added(previous.rule_ids, current.rule_ids)
        or _added(previous.source_ips, current.source_ips)
        or _added(previous.destination_ips, current.destination_ips)
        or _added(_names(previous.usernames), _names(current.usernames))
        or _added(previous.log_source_ids, current.log_source_ids)
    ):
        return True
    return current.event_count > previous.event_count and now - last_evaluated_at >= min_interval


def _added[T](before: Iterable[T], after: Iterable[T]) -> bool:
    return not set(after) <= set(before)


def _names(values: Iterable[str]) -> list[str]:
    return [value.casefold() for value in values]
