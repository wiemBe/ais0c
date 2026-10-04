"""The level rule (T-020 criterion 6): which evaluation of a case gets an e-mail."""

import pytest

from ais0c_contracts import Level
from ais0c_executor.email import ALERT_LEVELS, alert_needed

LOW, MEDIUM, HIGH, CRITICAL = Level.LOW, Level.MEDIUM, Level.HIGH, Level.CRITICAL


@pytest.mark.parametrize(
    ("level", "sent", "needed"),
    [
        # The first evaluation: only high and critical are e-mailed.
        (CRITICAL, [], True),
        (HIGH, [], True),
        (MEDIUM, [], False),
        (LOW, [], False),
        # A re-evaluation: only a level above every level already e-mailed.
        (HIGH, [HIGH], False),
        (CRITICAL, [HIGH], True),
        (CRITICAL, [CRITICAL], False),
        (HIGH, [CRITICAL], False),
        (CRITICAL, [HIGH, CRITICAL], False),
        (CRITICAL, [CRITICAL, HIGH], False),
        (MEDIUM, [HIGH], False),
        (LOW, [CRITICAL], False),
    ],
)
def test_a_case_is_emailed_again_only_when_its_level_goes_up(
    level: Level, sent: list[Level], needed: bool
) -> None:
    assert alert_needed(level, sent) is needed


def test_levels_compare_by_severity_not_as_strings() -> None:
    # As strings "critical" sorts before "high".
    assert Level.CRITICAL < Level.HIGH
    assert alert_needed(Level.CRITICAL, [Level.HIGH]) is True
    assert alert_needed(Level.HIGH, [Level.CRITICAL]) is False


def test_the_earlier_levels_can_come_in_any_iterable() -> None:
    assert alert_needed(Level.CRITICAL, (level for level in [Level.HIGH])) is True
    assert alert_needed(Level.HIGH, {Level.HIGH}) is False
    assert alert_needed(Level.HIGH) is True


def test_only_high_and_critical_are_alert_levels() -> None:
    assert frozenset({Level.HIGH, Level.CRITICAL}) == ALERT_LEVELS


def test_an_unknown_level_is_an_error() -> None:
    with pytest.raises(ValueError, match="severe"):
        alert_needed("severe", [])  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="severe"):
        alert_needed(Level.CRITICAL, ["severe"])  # pyright: ignore[reportArgumentType]
