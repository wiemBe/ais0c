"""bound_query: time window and LIMIT for compiled queries.

The guard tests import ais0c_policy. The cross-package tests in the root tests/ directory
are outside this task's allowed paths, and import-linter checks only package code.
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ais0c_contracts import TimeWindow
from ais0c_policy import AqlProfile, AqlRejectReason, check_aql
from ais0c_querylang import bound_query, compile_sigma, load_pipeline

TESTS = Path(__file__).resolve().parent
PIPELINE_PATH = TESTS.parents[2] / "config/sigma/qradar-pipeline.yaml"
H2_RULE = TESTS / "rules/h2_dcsync.yml"

ISTANBUL = timezone(timedelta(hours=3))
DAY = TimeWindow(
    start=datetime(2026, 9, 1, tzinfo=UTC),
    end=datetime(2026, 9, 2, tzinfo=UTC),
)
HUNT_PROFILE = AqlProfile(
    max_window=timedelta(days=1),
    max_limit=10_000,
    allowed_tables=frozenset({"events"}),
    wide_window_threshold=timedelta(hours=6),
)


def test_appends_limit_then_start_stop_in_the_given_time_zone() -> None:
    query = bound_query("SELECT * FROM events WHERE x=1", window=DAY, limit=500, tz=ISTANBUL)
    assert query == (
        "SELECT * FROM events WHERE x=1 LIMIT 500 "
        "START '2026-09-01 03:00:00' STOP '2026-09-02 03:00:00'"
    )


def test_window_is_widened_to_whole_seconds() -> None:
    window = TimeWindow(
        start=datetime(2026, 9, 1, 0, 0, 0, 900_000, tzinfo=UTC),
        end=datetime(2026, 9, 1, 0, 5, 0, 100_000, tzinfo=UTC),
    )
    query = bound_query("SELECT * FROM events", window=window, limit=1, tz=UTC)
    assert query.endswith("START '2026-09-01 00:00:00' STOP '2026-09-01 00:05:01'")


@pytest.mark.parametrize("limit", [0, -1])
def test_limit_must_be_positive(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        bound_query("SELECT * FROM events", window=DAY, limit=limit, tz=UTC)


def test_window_must_not_be_empty() -> None:
    empty = TimeWindow(start=DAY.end, end=DAY.start)
    with pytest.raises(ValueError, match="empty"):
        bound_query("SELECT * FROM events", window=empty, limit=10, tz=UTC)


# --- acceptance criterion 3: the output passes the AQL Guard ---------------------------------


def test_bounded_h2_query_passes_the_aql_guard() -> None:
    compiled = compile_sigma(H2_RULE.read_text(encoding="utf-8"), load_pipeline(PIPELINE_PATH))
    query = bound_query(compiled, window=DAY, limit=1_000, tz=ISTANBUL)

    result = check_aql(query, HUNT_PROFILE, indexed_fields=["devicetype", "username"])

    assert result.allowed, result.reasons
    assert result.window is not None
    assert result.window.duration == timedelta(days=1)


def test_guard_still_applies_its_rules_to_a_bounded_query() -> None:
    compiled = compile_sigma(H2_RULE.read_text(encoding="utf-8"), load_pipeline(PIPELINE_PATH))
    query = bound_query(compiled, window=DAY, limit=1_000, tz=ISTANBUL)

    result = check_aql(query, HUNT_PROFILE, indexed_fields=[])

    assert result.reasons == (AqlRejectReason.WIDE_WINDOW_UNINDEXED_FILTER,)


def test_bounding_a_bounded_query_is_rejected_by_the_guard() -> None:
    once = bound_query("SELECT * FROM events WHERE username='a'", window=DAY, limit=10, tz=UTC)
    twice = bound_query(once, window=DAY, limit=10, tz=UTC)
    result = check_aql(twice, HUNT_PROFILE, indexed_fields=["username"])
    assert not result.allowed
