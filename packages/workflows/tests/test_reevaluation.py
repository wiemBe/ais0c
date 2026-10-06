"""`should_reevaluate` and `reevaluation_due`: which offense updates are evaluated again, and
when (T-014 criterion 1, D-31; T-026 criterion 9, T-30 (1))."""

from datetime import UTC, datetime, timedelta

import pytest
from workflow_fakes import offense

from ais0c_contracts import OffenseSnapshot
from ais0c_workflows import reevaluation_due, should_reevaluate

START = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
EVALUATED_AT = START + timedelta(minutes=2)
INTERVAL = timedelta(minutes=30)
# The snapshot the last evaluation used.
EVALUATED = offense(101, start=START, event_count=12)


def updated(minutes: int, **changes: object) -> OffenseSnapshot:
    """The offense `minutes` after the evaluation, with `changes` to its fields."""
    return EVALUATED.model_copy(
        update={"last_updated_time": EVALUATED_AT + timedelta(minutes=minutes), **changes}
    )


def decide(current: OffenseSnapshot, *, after: timedelta) -> bool:
    return should_reevaluate(
        EVALUATED,
        current,
        last_evaluated_at=EVALUATED_AT,
        now=EVALUATED_AT + after,
        min_interval=INTERVAL,
    )


SOON = timedelta(minutes=1)


def test_a_new_rule_is_evaluated_at_once() -> None:
    assert decide(updated(1, rule_ids=[100201, 100305]), after=SOON)


def test_a_new_source_ip_is_evaluated_at_once() -> None:
    assert decide(updated(1, source_ips=["203.0.113.7", "203.0.113.99"]), after=SOON)


def test_a_new_destination_ip_is_evaluated_at_once() -> None:
    assert decide(updated(1, destination_ips=["198.51.100.15", "192.0.2.20"]), after=SOON)


def test_a_new_user_name_is_evaluated_at_once() -> None:
    assert decide(updated(1, usernames=["svc_backup_7731"]), after=SOON)


def test_a_new_log_source_is_evaluated_at_once() -> None:
    assert decide(updated(1, log_source_ids=[112, 413]), after=SOON)


def test_more_events_are_evaluated_once_the_interval_has_passed() -> None:
    assert decide(updated(31, event_count=40), after=timedelta(minutes=31))
    # The interval is inclusive.
    assert decide(updated(30, event_count=13), after=INTERVAL)


def test_more_events_within_the_interval_are_not_evaluated() -> None:
    assert not decide(updated(10, event_count=40), after=timedelta(minutes=10))
    assert not decide(updated(29, event_count=400), after=timedelta(minutes=29, seconds=59))


def test_the_interval_is_the_one_given() -> None:
    current = updated(31, event_count=40)
    later = EVALUATED_AT + timedelta(minutes=31)

    assert not should_reevaluate(
        EVALUATED,
        current,
        last_evaluated_at=EVALUATED_AT,
        now=later,
        min_interval=timedelta(minutes=45),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"magnitude": 9},
        {"event_count": 7},
        {"description": "Excessive Firewall Accepts From Single Source (updated)"},
        # Values that disappear are nothing new to look at.
        {"source_ips": [], "destination_ips": [], "rule_ids": []},
        # The same user in another case.
        {"usernames": ["SVC_BACKUP"]},
    ],
)
def test_an_update_without_anything_new_is_not_evaluated_even_after_the_interval(
    changes: dict[str, object],
) -> None:
    base = EVALUATED.model_copy(update={"usernames": ["svc_backup"]})
    current = base.model_copy(
        update={"last_updated_time": EVALUATED_AT + timedelta(hours=2), **changes}
    )

    assert not should_reevaluate(
        base,
        current,
        last_evaluated_at=EVALUATED_AT,
        now=EVALUATED_AT + timedelta(hours=2),
        min_interval=INTERVAL,
    )


# --- reevaluation_due (T-026 criterion 9, T-30 (1)) -------------------------------------------


def due(current: OffenseSnapshot) -> datetime | None:
    return reevaluation_due(
        EVALUATED, current, last_evaluated_at=EVALUATED_AT, min_interval=INTERVAL
    )


def test_an_update_with_something_new_is_due_at_once() -> None:
    assert due(updated(1, rule_ids=[100201, 100305])) == EVALUATED_AT
    assert due(updated(1, usernames=["SVC_BACKUP_7731"], event_count=40)) == EVALUATED_AT


def test_more_events_are_due_when_the_interval_ends() -> None:
    assert due(updated(1, event_count=40)) == EVALUATED_AT + INTERVAL
    assert due(updated(45, event_count=13)) == EVALUATED_AT + INTERVAL


def test_an_update_with_nothing_to_evaluate_is_never_due() -> None:
    assert due(updated(1)) is None
    assert due(updated(40, magnitude=9, source_ips=[])) is None
