"""The recorder (T-052 criteria 2 and 3), without a lab: a reader that answers from a table with the
replay engine stands in for the gateway. The lab test is at the end and runs only with the lab's
settings."""

import asyncio
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from ais0c_contracts import (
    EnrichmentContext,
    OffenseSnapshot,
    TimeWindow,
    ToolCoverage,
    ToolResult,
    ToolStatus,
)
from ais0c_harness.eval.cli import Dependencies, main
from ais0c_harness.replay.aql import run_query
from ais0c_harness.replay.record import (
    DEFAULT_EXCLUDED,
    RecordError,
    audit_queries,
    build_recording,
    event_window,
    fetch,
    read_events,
    same_answer,
)
from ais0c_harness.replay.recording import RecordedEvent, load_recording

from .replay_helpers import (
    BASE_MS,
    CALLS,
    ENRICHMENT,
    EVENTS,
    NOW,
    OFFENSE,
    event,
    raw_recording,
)

HEALTH = "Health Metrics"


class FakeReader:
    """The lab, as a table the replay engine answers from."""

    def __init__(self, events: list[RecordedEvent]) -> None:
        self.table = [item.model_dump(mode="json") for item in events]
        self.queries: list[str] = []

    async def offense(self, offense_id: int) -> OffenseSnapshot:
        assert offense_id == OFFENSE.offense_id
        return OFFENSE

    async def enrichment(self, offense: OffenseSnapshot) -> EnrichmentContext:
        return ENRICHMENT

    async def read_tool(
        self, offense: OffenseSnapshot, tool_id: str, arguments: dict[str, JsonValue]
    ) -> ToolResult:
        call = next((item for item in CALLS if item.tool_id == tool_id), None)
        if call is None:
            return ToolResult(
                status=ToolStatus.ERROR,
                deny_reason="upstream_error: not in the fake lab",
                data=[],
                truncated=False,
                coverage=ToolCoverage(complete=False, gaps=[]),
            )
        return call.result

    async def ariel(
        self, offense: OffenseSnapshot, window: TimeWindow, query: str
    ) -> list[dict[str, JsonValue]]:
        self.queries.append(query)
        return [dict(row) for row in run_query(query, self.table, now=NOW).rows]


def health(offset: int) -> RecordedEvent:
    return event(
        offset,
        qid=1,
        qidname="Metric",
        logsourceid=69,
        logsourcename="Health Metrics-2",
        typename=HEALTH,
        devicetype=368,
        sourceip="192.0.2.99",
        username=None,
        payload="metric",
    )


def test_the_events_come_back_complete_and_in_order_when_one_search_cannot_hold_them() -> None:
    many = [event(offset, sourceip="198.51.100.9") for offset in range(0, 2500 * 7, 7)]
    reader = FakeReader(many)

    rows = asyncio.run(read_events(reader, OFFENSE, event_window(OFFENSE), DEFAULT_EXCLUDED))

    assert [row["starttime"] for row in rows] == [
        BASE_MS + offset for offset in range(0, 2500 * 7, 7)
    ]
    assert len(reader.queries) > 3  # the first search was full and the window was split


def test_the_excluded_types_are_left_out_of_the_table() -> None:
    reader = FakeReader([*EVENTS, *(health(offset) for offset in range(50))])

    rows = asyncio.run(read_events(reader, OFFENSE, event_window(OFFENSE), DEFAULT_EXCLUDED))

    assert len(rows) == len(EVENTS)
    assert HEALTH not in {str(row["logsourcetypename"]) for row in rows}


def test_more_than_the_limit_in_one_second_is_refused_not_lost() -> None:
    reader = FakeReader([event(0, sourceip="198.51.100.9") for _ in range(1200)])

    with pytest.raises(RecordError, match="more than 1000 events within one second"):
        asyncio.run(read_events(reader, OFFENSE, event_window(OFFENSE), DEFAULT_EXCLUDED))


def test_a_fetched_recording_is_written_and_its_audits_agree(tmp_path: Path) -> None:
    reader = FakeReader([*EVENTS, *(health(offset) for offset in range(30))])

    raw = asyncio.run(fetch(reader, OFFENSE.offense_id))
    manifest = build_recording(
        raw, tmp_path / "fetched-77", recorded_at=NOW, gateway_version="g", fork_version="f"
    )
    recording = load_recording(tmp_path / "fetched-77")

    assert manifest.events == len(EVENTS)
    assert recording.manifest.excluded == [HEALTH]
    assert recording.manifest.window == event_window(OFFENSE)
    assert (recording.manifest.gateway_version, recording.manifest.fork_version) == ("g", "f")
    assert [call.tool_id for call in recording.calls] == ["get_offense", "get_rule"]
    assert len(recording.audits) >= 5
    needed = ("COUNT(*)", "QIDNAME(qid)", "GROUP BY", "ILIKE", "START ")
    assert all(any(marker in audit.query for audit in recording.audits) for marker in needed)
    # The lab's answers sit next to the queries; the engine gives the same on the recorded table.
    for audit in recording.audits:
        ours = run_query(audit.query, recording.event_rows, now=NOW).rows
        assert same_answer(audit.query, [dict(row) for row in ours], audit.rows), audit.name


def test_every_audit_query_passes_the_investigate_guard() -> None:
    from ais0c_mcp_gateway.registry import load_registry
    from ais0c_policy import check_aql

    from .eval_helpers import REPO_ROOT

    profile = load_registry(REPO_ROOT / "config", ("qradar",)).profiles["qradar-investigate-read"]
    assert profile.aql is not None
    for name, query in audit_queries(OFFENSE, event_window(OFFENSE), DEFAULT_EXCLUDED):
        guard = check_aql(query, profile.aql, profile.connector.indexed_fields)
        assert guard.allowed, (name, guard.reasons)


def test_rows_of_an_equal_sort_key_may_come_in_any_order() -> None:
    query = "SELECT starttime, sourceip FROM events ORDER BY starttime ASC LIMIT 9 START 1 STOP 2"
    one = [
        {"starttime": 1, "sourceip": "a"},
        {"starttime": 1, "sourceip": "b"},
        {"starttime": 2, "sourceip": "c"},
    ]
    shuffled = [one[1], one[0], one[2]]
    wrong_order = [one[2], one[0], one[1]]

    assert same_answer(query, one, shuffled)
    assert not same_answer(query, one, wrong_order)
    assert not same_answer(query, one, one[:2])


def test_the_default_excluded_type_is_qradars_own_metrics() -> None:
    assert DEFAULT_EXCLUDED == (HEALTH,)


# --- the command ---------------------------------------------------------------------------------


ENV = {
    "AIS0C_GATEWAY_URL": "http://127.0.0.1:8090",
    "AIS0C_WORKER_SECRETS_DIR": "/nonexistent",
    "AIS0C_DATABASE_URL": "postgresql+psycopg://x@127.0.0.1/x",
}


def record_cli(tmp_path: Path, recorder, *extra: str, env=None) -> tuple[int, str, str]:  # noqa: ANN001
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["--root", str(tmp_path), "record", "--offense", "77", "--out", "rec/lab-77-test", *extra],
        environ=ENV if env is None else env,
        deps=Dependencies(recorder=recorder),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_the_record_command_passes_its_settings_to_the_recorder(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    async def recorder(**kwargs: object):  # noqa: ANN202
        seen.update(kwargs)
        return build_recording(
            raw_recording(),
            Path(str(kwargs["directory"])),
            recorded_at=datetime(2026, 10, 7, tzinfo=UTC),
        )

    code, out, _ = record_cli(
        tmp_path, recorder, "--domain", "lab.example", "--host", "DC-LAB-01", "--host", "DC-LAB-02"
    )

    assert code == 0
    assert "recording lab-77-test: offense 77, 6 events" in out
    assert seen["offense_id"] == 77
    assert seen["domains"] == ["lab.example"]
    assert seen["hosts"] == ["DC-LAB-01", "DC-LAB-02"]
    assert seen["excluded"] == list(DEFAULT_EXCLUDED)
    assert seen["gateway_url"] == ENV["AIS0C_GATEWAY_URL"]
    assert seen["directory"] == tmp_path / "rec" / "lab-77-test"


def test_the_record_command_can_keep_every_type_or_name_its_own(tmp_path: Path) -> None:
    seen: list[object] = []

    async def recorder(**kwargs: object):  # noqa: ANN202
        seen.append(kwargs["excluded"])
        raise RecordError("stop")

    record_cli(tmp_path, recorder, "--keep-all-types")
    record_cli(tmp_path, recorder, "--exclude-type", "A", "--exclude-type", "B")

    assert seen == [[], ["A", "B"]]


def test_the_record_command_needs_the_dev_stack_settings(tmp_path: Path) -> None:
    async def recorder(**kwargs: object):  # noqa: ANN202
        raise AssertionError("must not run")

    code, _, err = record_cli(tmp_path, recorder, env={})

    assert code == 2
    assert "AIS0C_GATEWAY_URL" in err
    assert "AIS0C_DATABASE_URL" in err


def test_the_record_command_never_overwrites_a_recording(tmp_path: Path) -> None:
    target = tmp_path / "rec" / "lab-77-test"
    target.mkdir(parents=True)
    (target / "x").write_text("x", encoding="utf-8")

    async def recorder(**kwargs: object):  # noqa: ANN202
        raise AssertionError("must not run")

    code, _, err = record_cli(tmp_path, recorder)

    assert code == 2
    assert "never overwritten" in err


def test_a_recording_that_cannot_be_made_exits_one(tmp_path: Path) -> None:
    async def recorder(**kwargs: object):  # noqa: ANN202
        raise RecordError("the replay engine and the lab disagree: count_all")

    code, _, err = record_cli(tmp_path, recorder)

    assert code == 1
    assert "disagree: count_all" in err


# --- the committed lab recording ---------------------------------------------------------------------


def test_the_recording_of_lab_offense_30_is_in_the_repository_and_agrees_with_the_lab() -> None:
    """Criterion 3: `record` wrote harness/recordings/lab-30-dcsync from the planner's offense 30.
    It needs no lab to read: the lab's answers to the audit queries are in the recording, and the
    replay engine gives the same on the recorded table (test_replay_lab.py records again)."""
    from .eval_helpers import REPO_ROOT

    directory = REPO_ROOT / "harness" / "recordings" / "lab-30-dcsync"
    recording = load_recording(directory)

    assert recording.manifest.offense_id == 30
    assert recording.offense.offense_source == "svc_backup"
    assert recording.manifest.events > 10_000
    assert recording.manifest.excluded == [HEALTH]
    names = {audit.name for audit in recording.audits}
    assert {"count_all", "qidname_filter", "group_count", "ilike_payload", "epoch_window"} <= names
    for audit in recording.audits:
        ours = run_query(audit.query, recording.event_rows, now=NOW).rows
        assert same_answer(audit.query, [dict(row) for row in ours], audit.rows), audit.name


def test_the_manifests_versions_come_from_the_package_and_the_connector() -> None:
    from ais0c_harness.replay.record import versions

    from .eval_helpers import REPO_ROOT

    gateway, fork = versions(REPO_ROOT)

    assert gateway.startswith("0.1.0+registry.")
    assert len(gateway.split(".")[-1]) == 12
    assert len(fork) == 40
    assert fork == "7dcf3ce72062978e74975c7bca4f77e21d383a68"
