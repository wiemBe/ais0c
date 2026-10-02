"""AQL helpers."""

from datetime import datetime, timedelta, tzinfo

from ais0c_contracts import TimeWindow

# One of the START/STOP formats QRadar documents ("Time criteria in AQL queries").
_AQL_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def bound_query(query: str, *, window: TimeWindow, limit: int, tz: tzinfo) -> str:
    """Append `LIMIT` and a `START`/`STOP` window to a query that has neither.

    The result is `<query> LIMIT <limit> START '<start>' STOP '<stop>'`; AQL requires
    `LIMIT` before `START`. QRadar reads the START/STOP literals in its own time zone, so `tz`
    must be the time zone of the QRadar console. The window is widened to whole seconds.

    The query itself is not checked: a query that already has a `LIMIT` or a time clause
    ends up with two, and the AQL Guard rejects it.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    start = window.start.replace(microsecond=0)
    stop = _ceil_to_second(window.end)
    if stop <= start:
        raise ValueError("the time window is empty")
    return (
        f"{query.strip()} LIMIT {limit} "
        f"START '{start.astimezone(tz):{_AQL_TIME_FORMAT}}' "
        f"STOP '{stop.astimezone(tz):{_AQL_TIME_FORMAT}}'"
    )


def _ceil_to_second(value: datetime) -> datetime:
    if value.microsecond == 0:
        return value
    return value.replace(microsecond=0) + timedelta(seconds=1)
