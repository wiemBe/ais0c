"""AQL Guard: accepted queries, every rejection rule, normalization and query_hash."""

# The tests build AQL from fixed fragments on purpose.
# ruff: noqa: S608

import hashlib
from collections.abc import Iterable
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from ais0c_policy import AqlGuardResult, AqlProfile, AqlRejectReason, check_aql

R = AqlRejectReason

INVESTIGATION = AqlProfile(
    max_window=timedelta(days=7),
    max_limit=1_000,
    allowed_tables=frozenset({"events"}),
    wide_window_threshold=timedelta(days=1),
)
INDEXED = ("sourceip", "username", "devicetype", "Event ID")
GOOD = (
    "SELECT sourceip, username, COUNT(*) AS n FROM events "
    "WHERE username = 'svc_backup' AND LOWER(\"Object Properties\") LIKE '%1131f6ad%' "
    "GROUP BY sourceip, username ORDER BY n DESC LIMIT 100 LAST 2 DAYS"
)


def check(query: str, indexed: Iterable[str] = INDEXED) -> AqlGuardResult:
    return check_aql(query, INVESTIGATION, indexed)


def rejected_for(query: str, indexed: Iterable[str] = INDEXED) -> tuple[AqlRejectReason, ...]:
    result = check(query, indexed)
    assert not result.allowed
    assert result.reasons
    return result.reasons


# --- accepted queries -----------------------------------------------------------------------


def test_well_formed_query_is_allowed() -> None:
    result = check(GOOD)
    assert result.allowed
    assert result.reasons == ()
    assert result.normalized_query == GOOD
    assert result.query_hash == hashlib.sha256(GOOD.encode()).hexdigest()
    assert result.window is not None
    assert result.window.clause == "last"
    assert result.window.duration == timedelta(days=2)


@pytest.mark.parametrize(
    ("start", "stop"),
    [
        ("2026-09-01 10:00", "2026-09-03 10:30"),
        ("2026-09-01 10:00:00", "2026-09-03 10:30:00"),
        ("2026/09/01 10:00:00", "2026/09/03 10:30:00"),
        ("2026/09/01-10:00:00", "2026/09/03-10:30:00"),
        ("2026:09:01-10:00:00", "2026:09:03-10:30:00"),
    ],
)
def test_start_stop_window_is_computed(start: str, stop: str) -> None:
    query = (
        f"SELECT * FROM events WHERE sourceip = '192.0.2.10' LIMIT 50 START '{start}' STOP '{stop}'"
    )
    result = check(query)
    assert result.allowed, result.reasons
    assert result.window is not None
    assert result.window.clause == "start_stop"
    assert result.window.start == datetime(2026, 9, 1, 10, 0)  # noqa: DTZ001
    assert result.window.stop == datetime(2026, 9, 3, 10, 30)  # noqa: DTZ001
    assert result.window.duration == timedelta(days=2, minutes=30)


@pytest.mark.parametrize(
    ("clause", "duration"),
    [
        ("LAST 90 MINUTES", timedelta(minutes=90)),
        ("LAST 1 HOURS", timedelta(hours=1)),
        ("last 3 days", timedelta(days=3)),
        ("LAST 1 DAY", timedelta(days=1)),
    ],
)
def test_last_window_is_computed(clause: str, duration: timedelta) -> None:
    result = check(f"SELECT * FROM events WHERE username = 'a' LIMIT 10 {clause}")
    assert result.allowed, result.reasons
    assert result.window is not None
    assert result.window.duration == duration


def test_narrow_window_may_filter_on_unindexed_fields_only() -> None:
    query = "SELECT * FROM events WHERE \"Object Properties\" ILIKE '%x%' LIMIT 10 LAST 1 DAYS"
    assert check(query).allowed


def test_narrow_window_may_have_no_filter() -> None:
    assert check("SELECT * FROM events LIMIT 10 LAST 15 MINUTES").allowed


@pytest.mark.parametrize(
    "where",
    [
        "username = 'a' AND \"Object Properties\" ILIKE '%x%'",
        "\"Object Properties\" ILIKE '%x%' AND \"event id\" = '4662'",  # case-insensitive
        "LOWER(username) LIKE '%adm%'",
        "INCIDR('192.0.2.0/24', sourceip)",
    ],
)
def test_wide_window_with_an_indexed_filter_is_allowed(where: str) -> None:
    assert check(f"SELECT * FROM events WHERE {where} LIMIT 10 LAST 5 DAYS").allowed


def test_table_name_is_case_insensitive() -> None:
    assert check("SELECT * FROM Events WHERE username = 'a' LIMIT 10 LAST 5 MINUTES").allowed


# --- acceptance criterion 5: one test per rejection -------------------------------------------


def test_rejects_query_without_time_bound() -> None:
    assert rejected_for("SELECT * FROM events WHERE username = 'a' LIMIT 10") == (
        R.MISSING_TIME_BOUND,
    )


def test_rejects_query_without_limit() -> None:
    assert rejected_for("SELECT * FROM events WHERE username = 'a' LAST 5 MINUTES") == (
        R.MISSING_LIMIT,
    )


def test_rejects_limit_above_profile() -> None:
    assert rejected_for("SELECT * FROM events WHERE username = 'a' LIMIT 1001 LAST 5 MINUTES") == (
        R.LIMIT_EXCEEDS_PROFILE,
    )


@pytest.mark.parametrize(
    "time_clause",
    [
        "LAST 8 DAYS",
        "LAST 10081 MINUTES",
        "START '2026-09-01 00:00' STOP '2026-09-08 00:01'",
        "LAST 99999999999999999999 DAYS",
    ],
)
def test_rejects_window_wider_than_profile(time_clause: str) -> None:
    query = f"SELECT * FROM events WHERE username = 'a' LIMIT 10 {time_clause}"
    assert rejected_for(query) == (R.WINDOW_EXCEEDS_PROFILE,)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM events LIMIT 10 LAST 5 MINUTES",
        "(SELECT * FROM events LIMIT 10 LAST 5 MINUTES)",
        "WITH x AS y SELECT * FROM events LIMIT 10 LAST 5 MINUTES",
        "'SELECT' * FROM events LIMIT 10 LAST 5 MINUTES",
    ],
)
def test_rejects_query_not_starting_with_select(query: str) -> None:
    assert R.NOT_SELECT in rejected_for(query)


@pytest.mark.parametrize("table", ["flows", "FLOWS", "simarc", "events_archive"])
def test_rejects_table_outside_the_profile(table: str) -> None:
    query = f"SELECT * FROM {table} WHERE username = 'a' LIMIT 10 LAST 5 MINUTES"
    assert rejected_for(query) == (R.TABLE_NOT_ALLOWED,)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM events LIMIT 10 LAST 5 MINUTES; SELECT * FROM events LIMIT 10 LAST 5 MINUTES",
        "SELECT * FROM events LIMIT 10 LAST 5 MINUTES;",
        "SELECT * FROM events; LIMIT 10 LAST 5 MINUTES",
    ],
)
def test_rejects_multiple_statements(query: str) -> None:
    assert R.MULTIPLE_STATEMENTS in rejected_for(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM events WHERE payload ILIKE '%LAST 5 MINUTES%' LIMIT 10",
        "SELECT * FROM events WHERE x = 'START ''2026-09-01 00:00'' STOP ''2026-09-01 01:00''' LIMIT 10",
        "SELECT * FROM events WHERE x = 'it''s LAST 5 MINUTES' LIMIT 10",
        'SELECT "LAST 5 MINUTES" FROM events LIMIT 10',
    ],
)
def test_rejects_time_bound_that_is_only_inside_a_literal(query: str) -> None:
    assert rejected_for(query) == (R.MISSING_TIME_BOUND,)


def test_rejects_limit_that_is_only_inside_a_literal() -> None:
    query = "SELECT * FROM events WHERE x = 'LIMIT 10' LAST 5 MINUTES"
    assert rejected_for(query) == (R.MISSING_LIMIT,)


@pytest.mark.parametrize(
    "where",
    [
        "\"Object Properties\" ILIKE '%1131f6ad%'",
        "LOWER(\"Object Properties\") LIKE '%x%' OR \"Process Path\" = 'a'",
        "payload ILIKE '%mimikatz%'",
    ],
)
def test_rejects_wide_window_filtering_only_unindexed_fields(where: str) -> None:
    query = f"SELECT * FROM events WHERE {where} LIMIT 10 LAST 2 DAYS"
    assert rejected_for(query) == (R.WIDE_WINDOW_UNINDEXED_FILTER,)


def test_rejects_wide_window_without_a_filter() -> None:
    assert rejected_for("SELECT * FROM events LIMIT 10 LAST 2 DAYS") == (
        R.WIDE_WINDOW_UNINDEXED_FILTER,
    )


def test_indexed_field_outside_where_does_not_count() -> None:
    query = "SELECT username FROM events WHERE \"Object Properties\" = 'x' GROUP BY username LIMIT 10 LAST 2 DAYS"
    assert rejected_for(query) == (R.WIDE_WINDOW_UNINDEXED_FILTER,)


# --- input the guard refuses to interpret -----------------------------------------------------


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("", R.EMPTY_QUERY),
        ("   \n\t ", R.EMPTY_QUERY),
        ("SELECT * FROM events -- LIMIT 10 LAST 5 MINUTES", R.COMMENT_NOT_ALLOWED),
        ("SELECT * FROM events /* x */ LIMIT 10 LAST 5 MINUTES", R.COMMENT_NOT_ALLOWED),
        # QRadar might read \' as an escaped quote and keep the clauses inside the literal.
        ("SELECT * FROM events WHERE x = 'a\\' LIMIT 10 LAST 5 MINUTES --'", R.AMBIGUOUS_ESCAPE),
        ('SELECT "a\\" LIMIT 10 LAST 5 MINUTES" FROM events', R.AMBIGUOUS_ESCAPE),
        ("SELECT * FROM events WHERE x = 'open LIMIT 10 LAST 5 MINUTES", R.UNTERMINATED_LITERAL),
        ('SELECT "open FROM events LIMIT 10 LAST 5 MINUTES', R.UNTERMINATED_LITERAL),
        ("SELECT * FROM events WHERE (x = 1 LIMIT 10 LAST 5 MINUTES", R.UNBALANCED_PARENTHESES),
        ("SELECT * FROM events WHERE x = 1) LIMIT 10 LAST 5 MINUTES", R.UNBALANCED_PARENTHESES),
        ("SELECT * FROM events LIMIT 10 LAST 5MINUTES", R.UNEXPECTED_CHARACTER),
        (
            "SELECT * FROM events WHERE x = 'a''b'\"c\" LIMIT 10 LAST 5 MINUTES",
            R.UNEXPECTED_CHARACTER,
        ),
        ("SELECT * FROM events\x00 LIMIT 10 LAST 5 MINUTES", R.UNEXPECTED_CHARACTER),
        ("SELECT * FROM events\u00a0LIMIT 10 LAST 5 MINUTES", R.UNEXPECTED_CHARACTER),
        ("SELECT * FROM events WHERE x = `a` LIMIT 10 LAST 5 MINUTES", R.UNEXPECTED_CHARACTER),
        ("SELECT * FROM événements LIMIT 10 LAST 5 MINUTES", R.UNEXPECTED_CHARACTER),
    ],
)
def test_rejects_input_that_cannot_be_tokenized_unambiguously(
    query: str, reason: AqlRejectReason
) -> None:
    result = check(query)
    assert result.reasons == (reason,)
    assert not result.allowed
    assert result.normalized_query is None
    assert result.query_hash is None
    assert result.window is None


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM events WHERE sourceip IN (SELECT sourceip FROM flows) LIMIT 10 LAST 5 MINUTES",
        "SELECT (SELECT 1) FROM events LIMIT 10 LAST 5 MINUTES",
    ],
)
def test_rejects_nested_select(query: str) -> None:
    assert R.NESTED_SELECT in rejected_for(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * LIMIT 10 LAST 5 MINUTES",
        "SELECT * FROM events, flows LIMIT 10 LAST 5 MINUTES",
        "SELECT * FROM events JOIN flows LIMIT 10 LAST 5 MINUTES",
        "SELECT * FROM 'events' LIMIT 10 LAST 5 MINUTES",
        "SELECT * FROM events FROM events LIMIT 10 LAST 5 MINUTES",
    ],
)
def test_rejects_malformed_from(query: str) -> None:
    assert R.FROM_INVALID in rejected_for(query)


@pytest.mark.parametrize(
    "limit_clause",
    ["LIMIT 0", "LIMIT 1.5", "LIMIT x", "LIMIT", "LIMIT 10 LIMIT 5", "LIMIT -1"],
)
def test_rejects_invalid_limit(limit_clause: str) -> None:
    query = f"SELECT * FROM events WHERE username = 'a' {limit_clause} LAST 5 MINUTES"
    assert rejected_for(query) == (R.LIMIT_INVALID,)


def test_rejects_huge_limit_as_exceeding_the_profile() -> None:
    query = f"SELECT * FROM events WHERE username = 'a' LIMIT {'9' * 5000} LAST 5 MINUTES"
    assert rejected_for(query) == (R.LIMIT_EXCEEDS_PROFILE,)


def test_limit_and_time_bound_inside_parentheses_do_not_count() -> None:
    query = "SELECT * FROM events WHERE (username = 'a' LIMIT 10 LAST 5 MINUTES)"
    assert set(rejected_for(query)) == {R.MISSING_LIMIT, R.MISSING_TIME_BOUND}


@pytest.mark.parametrize(
    "time_clause",
    [
        "LAST 5 MINUTES START '2026-09-01 00:00' STOP '2026-09-01 01:00'",
        "LAST 5 MINUTES LAST 6 MINUTES",
        "START '2026-09-01 00:00'",
        "STOP '2026-09-01 00:00'",
        "START '2026-09-01 01:00' STOP '2026-09-01 00:00'",
        "START '2026-09-01 01:00' STOP '2026-09-01 01:00'",
        "START PARSEDATETIME('1 hour ago') STOP PARSEDATETIME('now')",
        "START '2026-09-01 00:00Z' STOP '2026-09-01 01:00Z'",
        "START 'yesterday' STOP 'today'",
        "LAST 5 WEEKS",
        "LAST 5 SECONDS",
        "LAST 0 MINUTES",
        "LAST x MINUTES",
        "LAST 1.5 HOURS",
        "LAST",
    ],
)
def test_rejects_invalid_time_bound(time_clause: str) -> None:
    query = f"SELECT * FROM events WHERE username = 'a' LIMIT 10 {time_clause}"
    assert rejected_for(query) == (R.TIME_BOUND_INVALID,)


def test_reports_every_reason() -> None:
    reasons = rejected_for("SELECT * FROM flows; DROP")
    assert set(reasons) == {
        R.MULTIPLE_STATEMENTS,
        R.FROM_INVALID,
        R.TABLE_NOT_ALLOWED,
        R.MISSING_LIMIT,
        R.MISSING_TIME_BOUND,
    }


# --- acceptance criterion 6: normalization and query_hash ---------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        # keyword case
        "select sourceip, username, count(*) as n from events "
        "where username = 'svc_backup' and lower(\"Object Properties\") like '%1131f6ad%' "
        "group by sourceip, username order by n desc limit 100 last 2 days",
        # whitespace
        "  SELECT\n\tsourceip ,username,COUNT( * )  AS n\nFROM events\r\n"
        "WHERE username='svc_backup'AND LOWER (\"Object Properties\")LIKE '%1131f6ad%'\n"
        "GROUP BY sourceip,username ORDER BY n DESC\nLIMIT 100\nLAST 2 DAYS\n",
        # both
        "Select sourceip,username,Count(*) As n From events Where username='svc_backup' "
        "And LOWER(\"Object Properties\") Like '%1131f6ad%' Group By sourceip,username "
        "Order By n Desc Limit 100 Last 2 Days",
    ],
)
def test_whitespace_and_keyword_case_do_not_change_the_hash(variant: str) -> None:
    original = check(GOOD)
    other = check(variant)
    assert other.normalized_query == original.normalized_query
    assert other.query_hash == original.query_hash


def test_literal_content_changes_the_hash() -> None:
    assert check(GOOD).query_hash != check(GOOD.replace("svc_backup", "SVC_BACKUP")).query_hash
    assert check(GOOD).query_hash != check(GOOD.replace("'svc_backup'", "'svc_backup '")).query_hash


def test_quoted_name_content_changes_the_hash() -> None:
    other = GOOD.replace('"Object Properties"', '"object properties"')
    assert check(GOOD).query_hash != check(other).query_hash


def test_normalization_is_idempotent() -> None:
    messy = (
        "select  *  from events\nwhere LOWER( username )like '%a%' and x>=1 limit 5 last 1 hours"
    )
    normalized = check(messy).normalized_query
    assert (
        normalized
        == "SELECT * FROM events WHERE LOWER(username) LIKE '%a%' AND x >= 1 LIMIT 5 LAST 1 HOURS"
    )
    assert check(normalized).normalized_query == normalized


def test_rejected_query_still_has_a_hash() -> None:
    result = check("SELECT * FROM flows LIMIT 10 LAST 5 MINUTES")
    assert not result.allowed
    assert result.normalized_query == "SELECT * FROM flows LIMIT 10 LAST 5 MINUTES"
    assert result.query_hash is not None


# --- profile ------------------------------------------------------------------------------------


def test_profile_lowercases_tables() -> None:
    profile = AqlProfile(
        max_window=timedelta(days=1),
        max_limit=10,
        allowed_tables=frozenset({"Events"}),
        wide_window_threshold=timedelta(hours=1),
    )
    assert profile.allowed_tables == frozenset({"events"})


@pytest.mark.parametrize(
    "override",
    [
        {"max_window": timedelta(0)},
        {"max_limit": 0},
        {"allowed_tables": frozenset[str]()},
        {"wide_window_threshold": timedelta(seconds=-1)},
        {"unknown": 1},
    ],
)
def test_profile_rejects_invalid_values(override: dict[str, object]) -> None:
    values: dict[str, object] = {
        "max_window": timedelta(days=1),
        "max_limit": 10,
        "allowed_tables": frozenset({"events"}),
        "wide_window_threshold": timedelta(hours=1),
    }
    with pytest.raises(ValidationError):
        AqlProfile.model_validate(values | override)
