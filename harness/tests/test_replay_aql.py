# ruff: noqa: S608 - AQL test queries, not SQL built from input
"""The replay engine's AQL subset (T-052 criterion 4): every operator and function, the result
names QRadar gives, the errors QRadar gives, and the constructs the engine refuses."""

from datetime import timedelta
from typing import Any

import pytest

from ais0c_harness.replay.aql import QueryError, Unsupported, format_time, run_query

from .replay_helpers import BASE_MS, EVENTS, NOW

TABLE = [event.model_dump(mode="json") for event in EVENTS]
WINDOW = f"START {BASE_MS - 1000} STOP {BASE_MS + 120_000}"


def rows(query: str) -> list[dict[str, object]]:
    return [dict(row) for row in run_query(query, TABLE, now=NOW).rows]


def column(query: str, name: str) -> list[Any]:
    return [row[name] for row in rows(query)]


def select(where: str, *, tail: str = "") -> list[object]:
    """The starttime offsets of the rows a condition selects, oldest first."""
    query = (
        f"SELECT starttime FROM events WHERE {where} ORDER BY starttime ASC {tail} "
        f"LIMIT 100 {WINDOW}"
    )
    return [int(str(value)) - BASE_MS for value in column(query, "starttime")]


# --- conditions -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("username = 'svc_backup'", [0, 1000, 2000]),
        ("username != 'svc_backup'", [3000]),
        ("username <> 'svc_backup'", [3000]),
        ("qid = 4624", [3000, 4000]),
        ("qid < 4625", [3000, 4000]),
        ("qid > 5000000", [0, 1000, 2000, 60_000]),
        ("qid <= 4624", [3000, 4000]),
        ("qid >= 38750003", [60_000]),
        ("sourceip IN ('198.51.100.23', '203.0.113.8')", [0, 4000]),
        (
            "sourceip NOT IN ('198.51.100.23', '198.51.100.24', '198.51.100.25')",
            [3000, 4000, 60_000],
        ),
        ("qidname LIKE 'Success Audit: An operation%'", None),
        ("QIDNAME(qid) LIKE 'Success Audit: An operation%'", [0, 1000, 2000]),
        ("QIDNAME(qid) ILIKE '%LOGGED ON'", [3000, 4000]),
        ("QIDNAME(qid) LIKE '%LOGGED ON'", []),
        ("QIDNAME(qid) NOT ILIKE '%object%'", [3000, 4000, 60_000]),
        ("eventcount BETWEEN 1 AND 1", [0, 1000, 2000, 3000, 4000, 60_000]),
        (
            "starttime BETWEEN " + str(BASE_MS + 500) + " AND " + str(BASE_MS + 3000),
            [1000, 2000, 3000],
        ),
        (
            "starttime NOT BETWEEN " + str(BASE_MS + 500) + " AND " + str(BASE_MS + 3000),
            [0, 4000, 60_000],
        ),
        ("username IS NULL", [4000, 60_000]),
        ("username IS NOT NULL", [0, 1000, 2000, 3000]),
        ("username = 'svc_backup' AND sourceip = '198.51.100.24'", [1000]),
        ("username = 'analyst1' OR sourceip = '198.51.100.25'", [2000, 3000]),
        ("NOT (qid = 4624)", [0, 1000, 2000, 60_000]),
        ("(qid = 4624 OR qid = 38750003) AND username IS NULL", [4000, 60_000]),
        ("UTF8(payload) ILIKE '%analyst1%'", [3000]),
        ("LOGSOURCENAME(logsourceid) = 'DC-01'", [0, 1000, 2000, 3000, 4000]),
        ("LOGSOURCETYPENAME(devicetype) = 'System Notification'", [60_000]),
        ("CATEGORYNAME(category) = 'Object Access'", [0, 1000, 2000, 3000, 4000, 60_000]),
        (
            "LOGSOURCETYPENAME(devicetype) NOT IN ('System Notification')",
            [0, 1000, 2000, 3000, 4000],
        ),
    ],
)
def test_where_operators(where: str, expected: list[int] | None) -> None:
    if expected is None:
        # qidname is not a column of events: QRadar refuses it.
        with pytest.raises(QueryError, match=r"qidname.*does not exist"):
            select(where)
        return

    assert select(where) == expected


def test_a_comparison_with_null_selects_nothing() -> None:
    assert select("username = NULL") == []
    assert select("username != 'x'") == [0, 1000, 2000, 3000]
    assert select("NOT (username = 'svc_backup')") == [3000]


def test_a_number_is_compared_with_a_string_as_text() -> None:
    assert select("qid = '4624'") == [3000, 4000]


# --- select list ------------------------------------------------------------------------------


def test_result_columns_are_named_as_qradar_names_them() -> None:
    result = run_query(
        "SELECT QIDNAME(qid), UTF8(payload), LOGSOURCENAME(logsourceid), "
        "DATEFORMAT(starttime, 'yyyy-MM-dd HH:mm:ss'), username, sourceip AS src "
        f"FROM events LIMIT 1 {WINDOW}",
        TABLE,
        now=NOW,
    )

    assert result.columns == (
        "qidname_qid",
        "utf8_payload",
        "logsourcename_logsourceid",
        "dateformat_starttime_yyyy_MM_dd_HH_mm_ss",
        "username",
        "src",
    )
    assert set(result.rows[0]) == set(result.columns)


def test_dateformat_formats_in_utc() -> None:
    value = column(
        f"SELECT DATEFORMAT(starttime, 'yyyy-MM-dd HH:mm:ss') AS t FROM events "
        f"ORDER BY starttime ASC LIMIT 1 {WINDOW}",
        "t",
    )

    assert value == ["2026-10-05 14:57:43"]
    assert format_time(BASE_MS, "yyyy-MM-dd'T'HH:mm:ss.SSS") == "2026-10-05T14:57:43.905"


def test_a_column_name_is_case_insensitive() -> None:
    assert column(f"SELECT UserName FROM events WHERE QID = 4624 LIMIT 5 {WINDOW}", "UserName") == [
        None,
        "analyst1",
    ]


def test_select_distinct_is_not_aql() -> None:
    with pytest.raises(QueryError, match='Field "DISTINCT" does not exist'):
        run_query(f"SELECT DISTINCT username FROM events LIMIT 9 {WINDOW}", TABLE, now=NOW)


# --- aggregates, GROUP BY, ORDER BY, LIMIT -----------------------------------------------------


def test_aggregates_are_doubles_with_qradars_names() -> None:
    (row,) = rows(
        "SELECT COUNT(*), COUNT(username), SUM(eventcount), MIN(starttime), MAX(starttime) "
        f"FROM events LIMIT 10 {WINDOW}"
    )

    assert row == {
        "COUNT": 6.0,
        "COUNT_username": 4.0,
        "SUM_eventcount": 6.0,
        "MIN_starttime": float(BASE_MS),
        "MAX_starttime": float(BASE_MS + 60_000),
    }
    assert all(isinstance(value, float) for value in row.values())


def test_an_aggregate_of_no_rows_is_zero_or_null() -> None:
    (row,) = rows(
        f"SELECT COUNT(*) AS n, MAX(qid) AS top FROM events WHERE qid = 1 LIMIT 5 {WINDOW}"
    )

    assert row == {"n": 0.0, "top": None}


def test_group_by_counts_each_group_and_orders_by_an_alias() -> None:
    result = rows(
        "SELECT username, COUNT(*) AS n FROM events WHERE username IS NOT NULL "
        f"GROUP BY username ORDER BY n DESC LIMIT 10 {WINDOW}"
    )

    assert result == [{"username": "svc_backup", "n": 3.0}, {"username": "analyst1", "n": 1.0}]


def test_a_column_that_is_not_grouped_is_first_valued() -> None:
    result = rows(
        f"SELECT logsourceid, devicetype, COUNT(*) AS n FROM events GROUP BY logsourceid "
        f"ORDER BY logsourceid ASC LIMIT 10 {WINDOW}"
    )

    assert result == [
        {"logsourceid": 10, "FIRST_devicetype": 12, "n": 5.0},
        {"logsourceid": 65, "FIRST_devicetype": 147, "n": 1.0},
    ]


def test_the_newest_event_comes_first_without_order_by() -> None:
    assert select("qid > 0", tail="") == [0, 1000, 2000, 3000, 4000, 60_000]
    newest = column(f"SELECT starttime FROM events LIMIT 2 {WINDOW}", "starttime")
    assert newest == [BASE_MS + 60_000, BASE_MS + 4000]


def test_order_by_descending_and_limit() -> None:
    assert column(
        f"SELECT starttime FROM events ORDER BY starttime DESC LIMIT 2 {WINDOW}", "starttime"
    ) == [BASE_MS + 60_000, BASE_MS + 4000]


def test_order_by_sorts_nulls_first() -> None:
    assert column(
        f"SELECT username FROM events ORDER BY username ASC LIMIT 3 {WINDOW}", "username"
    ) == [None, None, "analyst1"]


# --- time clause ------------------------------------------------------------------------------


def test_start_and_stop_in_epoch_milliseconds_are_inclusive() -> None:
    query = f"SELECT starttime FROM events LIMIT 99 START {BASE_MS + 1000} STOP {BASE_MS + 3000}"

    assert sorted(column(query, "starttime")) == [BASE_MS + 1000, BASE_MS + 2000, BASE_MS + 3000]


def test_start_and_stop_text_is_read_as_utc() -> None:
    inside = (
        "SELECT COUNT(*) AS n FROM events LIMIT 9 START '2026-10-05 14:57' STOP '2026-10-05 14:59'"
    )
    later = (
        "SELECT COUNT(*) AS n FROM events LIMIT 9 START '2026-10-05 14:59' STOP '2026-10-05 15:00'"
    )

    assert column(inside, "n") == [6.0]
    assert column(later, "n") == [0.0]


def test_last_counts_back_from_the_moment_of_the_evaluation() -> None:
    inside = "SELECT COUNT(*) AS n FROM events LIMIT 9 LAST 10 MINUTES"
    outside = "SELECT COUNT(*) AS n FROM events LIMIT 9 LAST 1 MINUTES"
    nothing = run_query(
        "SELECT COUNT(*) AS n FROM events LIMIT 9 LAST 1 HOURS", TABLE, now=NOW + timedelta(hours=2)
    ).rows

    assert column(inside, "n") == [6.0]
    assert column(outside, "n") == [0.0]  # NOW is five minutes after: the events are older
    assert nothing[0]["n"] == 0.0


# --- what QRadar refuses ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "message"),
    [
        (
            f"SELECT eventname FROM events LIMIT 1 {WINDOW}",
            'Field "eventname" does not exist in catalog "events"',
        ),
        (f"SELECT username FROM events WHERE devicetypeid = 1 LIMIT 1 {WINDOW}", "does not exist"),
        (f"SELECT QIDNAME(username) FROM events LIMIT 1 {WINDOW}", "Wrong argument type"),
        (f"SELECT UTF8(username) FROM events LIMIT 1 {WINDOW}", "Wrong argument type"),
        (f"SELECT DATEFORMAT(starttime) FROM events LIMIT 1 {WINDOW}", "Wrong argument type"),
        (f"SELECT username FROM events WHERE LIMIT 1 {WINDOW}", "Error Parsing"),
        (f"SELECT username FROM events WHERE username = 'x LIMIT 1 {WINDOW}", "Error Parsing"),
        ("SELECT username FROM events LIMIT 1 START 'not a date' STOP 'nor this'", "Error Parsing"),
        (f"SELECT username FROM events LIMIT x {WINDOW}", "Error Parsing"),
    ],
)
def test_queries_qradar_refuses_raise_a_query_error(query: str, message: str) -> None:
    with pytest.raises(QueryError, match=message):
        run_query(query, TABLE, now=NOW)


@pytest.mark.parametrize(
    "query",
    [
        f"SELECT * FROM events LIMIT 1 {WINDOW}",
        f"SELECT username FROM flows LIMIT 1 {WINDOW}",
        f"SELECT username FROM events WHERE username IN (SELECT username FROM events) LIMIT 1 {WINDOW}",
        f"SELECT COUNT(*) FROM events GROUP BY username HAVING COUNT(*) > 1 LIMIT 5 {WINDOW}",
        f"SELECT LOWER(username) FROM events LIMIT 1 {WINDOW}",
        f"SELECT eventcount + 1 FROM events LIMIT 1 {WINDOW}",
        f"SELECT username FROM events LIMIT 1 OFFSET 1 {WINDOW}",
        f"SELECT QIDNAME(qid) FROM events UNION SELECT 1 FROM events LIMIT 1 {WINDOW}",
        f"SELECT username FROM events WHERE username ~ 'x' LIMIT 1 {WINDOW}",
        # A field QRadar has and the recording does not hold; a custom property is unknown too.
        f"SELECT devicetime FROM events LIMIT 1 {WINDOW}",
        f"SELECT identityhostname FROM events LIMIT 1 {WINDOW}",
        f'SELECT "Process CommandLine" FROM events LIMIT 1 {WINDOW}',
        f"SELECT QIDNAME(qid) AS q, COUNT(*) FROM events GROUP BY username LIMIT 1 {WINDOW}",
    ],
)
def test_constructs_outside_the_subset_are_unsupported(query: str) -> None:
    with pytest.raises(Unsupported):
        run_query(query, TABLE, now=NOW)


def test_a_payload_compared_directly_is_refused_as_qradar_refuses_it() -> None:
    with pytest.raises(QueryError, match="byte"):
        run_query(
            f"SELECT username FROM events WHERE payload ILIKE '%4624%' LIMIT 5 {WINDOW}",
            TABLE,
            now=NOW,
        )
