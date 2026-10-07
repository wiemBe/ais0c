# ruff: noqa: S608 - AQL test queries, not SQL built from input
"""The replay gateway (T-052 criterion 5): the Ariel lifecycle, the gateway's own checks and
evidence, the recording's read tools and the derived answers."""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from functools import cache
from pathlib import Path

import pytest
from pydantic import JsonValue

from ais0c_contracts import (
    CostClass,
    CriticalAssetHit,
    Level,
    TimeWindow,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_harness.eval import AgentConfig, GatewayExchange, load_agent_config
from ais0c_harness.replay.gateway import ReplayGateway
from ais0c_harness.replay.recording import Recording
from ais0c_policy import check_aql

from .eval_helpers import REGISTRY, REPO_ROOT, gateway_profile_of, profile_of
from .replay_helpers import BASE_MS, NOW, START, event, write_synthetic

INVESTIGATE = "config/agents/investigation.yaml"
VERIFY = "config/agents/verification.yaml"
WINDOW = f"START {BASE_MS - 1000} STOP {BASE_MS + 120_000}"
USERS = f"SELECT username, sourceip FROM events WHERE username = 'svc_backup' LIMIT 100 {WINDOW}"


@cache
def config(manifest: str) -> AgentConfig:
    return load_agent_config(REPO_ROOT, manifest, REGISTRY)


@pytest.fixture
def recording(tmp_path: Path) -> Recording:
    return write_synthetic(tmp_path / "synthetic-77")


def gateway(recording: Recording, manifest: str = INVESTIGATE, run: str = "run-1") -> ReplayGateway:
    return ReplayGateway(
        gateway_profile_of(config(manifest)), recording, now=NOW, run_id=f"harness-test-{run}"
    )


def intent(
    tool_id: str,
    arguments: Mapping[str, JsonValue],
    *,
    manifest: str = INVESTIGATE,
    run: str = "run-1",
    window: TimeWindow | None = None,
    schema_version: str | None = None,
) -> ToolIntent:
    profile = profile_of(config(manifest))
    spec = next((tool for tool in profile.tools if tool.id == tool_id), None)
    return ToolIntent(
        run_id=f"harness-test-{run}",
        case_id="case-77",
        agent_id=config(manifest).manifest.id,
        toolset_profile=profile.name,
        tool_id=tool_id,
        tool_schema_version=schema_version or (spec.schema_version if spec else "0"),
        arguments=dict(arguments),
        reason="Look at the events of the offense.",
        expected_evidence="The events.",
        time_window=window or TimeWindow(start=START - timedelta(hours=1), end=NOW),
        cost_class=spec.cost_class if spec else CostClass.LOW,
    )


def call(replay: ReplayGateway, **kwargs: object) -> ToolResult:
    return asyncio.run(replay.call(intent(**kwargs)))  # type: ignore[arg-type]


def last(replay: ReplayGateway) -> GatewayExchange:
    return replay.exchanges[-1]


def started(replay: ReplayGateway, query: str = USERS, **kwargs: object) -> str:
    result = call(
        replay, tool_id="create_ariel_search", arguments={"query_expression": query}, **kwargs
    )
    assert result.status is ToolStatus.OK, result.deny_reason
    search_id = result.data[0]["search_id"]
    assert isinstance(search_id, str)
    return search_id


# --- the lifecycle --------------------------------------------------------------------------------


def test_a_search_runs_through_create_status_results_and_delete(recording: Recording) -> None:
    replay = gateway(recording)

    search_id = started(replay)
    status = call(replay, tool_id="get_ariel_search_status", arguments={"search_id": search_id})
    results = call(replay, tool_id="get_ariel_search_results", arguments={"search_id": search_id})
    deleted = call(replay, tool_id="delete_ariel_search", arguments={"search_id": search_id})

    assert status.data[0]["status"] == "COMPLETED"
    assert [row["sourceip"] for row in results.data] == [
        "198.51.100.25",
        "198.51.100.24",
        "198.51.100.23",
    ]
    assert deleted.status is ToolStatus.OK
    assert [exchange.outcome for exchange in replay.exchanges] == ["replayed"] * 4
    assert all(exchange.executed for exchange in replay.exchanges)


def test_the_search_id_is_deterministic_per_run_and_valid(recording: Recording) -> None:
    first, second, other = gateway(recording), gateway(recording), gateway(recording, run="run-2")

    ids = [started(first), started(second), started(other, run="run-2")]

    assert ids[0] == ids[1] != ids[2]
    assert all(len(item) == 36 for item in ids)


def test_a_search_of_another_run_or_never_made_is_denied(recording: Recording) -> None:
    mine, theirs = gateway(recording), gateway(recording, run="run-2")
    foreign = started(theirs, run="run-2")

    for search_id in (foreign, "00000000-0000-0000-0000-000000000000"):
        result = call(mine, tool_id="get_ariel_search_results", arguments={"search_id": search_id})
        assert result.status is ToolStatus.DENIED
        assert result.deny_reason is not None
        assert result.deny_reason.startswith("search_not_owned")
        assert not last(mine).executed


def test_a_deleted_search_is_refused(recording: Recording) -> None:
    replay = gateway(recording)
    search_id = started(replay)
    call(replay, tool_id="delete_ariel_search", arguments={"search_id": search_id})

    for tool_id in ("get_ariel_search_status", "get_ariel_search_results", "delete_ariel_search"):
        result = call(replay, tool_id=tool_id, arguments={"search_id": search_id})
        assert result.status is ToolStatus.ERROR
        assert result.deny_reason is not None
        assert result.deny_reason.startswith("upstream_error")


@pytest.mark.parametrize(("manifest", "max_rows"), [(INVESTIGATE, 500), (VERIFY, 200)])
def test_results_are_paged_and_clamped_to_the_profiles_max_rows(
    tmp_path: Path, manifest: str, max_rows: int
) -> None:
    many = [event(offset, sourceip="198.51.100.9", username="bulk") for offset in range(700)]
    replay = gateway(write_synthetic(tmp_path / "bulk-77", many), manifest)
    query = f"SELECT username FROM events WHERE username = 'bulk' LIMIT 200 {WINDOW}"
    if manifest == INVESTIGATE:
        query = query.replace("LIMIT 200", "LIMIT 1000")
    search_id = started(replay, query, manifest=manifest)

    first = call(
        replay,
        manifest=manifest,
        tool_id="get_ariel_search_results",
        arguments={"search_id": search_id, "limit": 10_000},
    )
    second = call(
        replay,
        manifest=manifest,
        tool_id="get_ariel_search_results",
        arguments={"search_id": search_id, "limit": 10_000, "start": max_rows},
    )

    total = 700 if manifest == INVESTIGATE else 200
    assert len(first.data) == max_rows
    assert first.truncated
    assert len(second.data) == total - max_rows
    assert not second.truncated
    assert not first.coverage.complete


# --- the gateway's own checks --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        (f"SELECT username FROM events {WINDOW}", "missing_limit"),
        ("SELECT username FROM events LIMIT 10", "missing_time_bound"),
        (f"SELECT username FROM events LIMIT 5000 {WINDOW}", "limit_exceeds_profile"),
        (f"SELECT username FROM events {WINDOW} LIMIT 10", "limit_after_time_bound"),
        (f"SELECT username FROM flows LIMIT 10 {WINDOW}", "table_not_allowed"),
        (
            f"SELECT username FROM events LIMIT 10 START {BASE_MS - 9 * 86_400_000} STOP {BASE_MS}",
            "window_exceeds_profile",
        ),
    ],
)
def test_the_aql_guard_denies_as_the_gateway_does(
    recording: Recording, query: str, reason: str
) -> None:
    replay = gateway(recording)

    result = call(replay, tool_id="create_ariel_search", arguments={"query_expression": query})

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason is not None
    assert result.deny_reason.startswith("aql_guard")
    assert reason in result.deny_reason
    assert last(replay).outcome == "denied"
    assert not last(replay).executed
    assert not replay.evidence


def test_the_verifier_may_not_query_a_filtered_field(recording: Recording) -> None:
    replay = gateway(recording, VERIFY)
    query = f"SELECT UTF8(payload) AS p FROM events WHERE username = 'svc_backup' LIMIT 10 {WINDOW}"

    result = call(
        replay,
        manifest=VERIFY,
        tool_id="create_ariel_search",
        arguments={"query_expression": query},
    )

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason is not None
    assert result.deny_reason.startswith("aql_filtered_field")


def test_the_verifier_runs_a_query_that_names_no_filtered_field(recording: Recording) -> None:
    replay = gateway(recording, VERIFY)
    query = f"SELECT username, sourceip FROM events WHERE username = 'svc_backup' LIMIT 10 {WINDOW}"

    search_id = started(replay, query, manifest=VERIFY)
    results = call(
        replay,
        manifest=VERIFY,
        tool_id="get_ariel_search_results",
        arguments={"search_id": search_id},
    )

    assert [sorted(row) for row in results.data] == [["sourceip", "username"]] * 3


def test_an_intent_the_gateway_would_not_accept_is_schema_invalid(recording: Recording) -> None:
    replay = gateway(recording)

    result = call(
        replay,
        tool_id="create_ariel_search",
        arguments={"query_expression": USERS},
        schema_version="stale",
    )

    assert result.status is ToolStatus.DENIED
    assert last(replay).outcome == "schema_invalid"


def test_a_tool_outside_the_profile_is_never_run(recording: Recording) -> None:
    replay = gateway(recording, VERIFY)

    result = call(replay, manifest=VERIFY, tool_id="get_offense", arguments={"offense_id": 77})

    assert result.status is ToolStatus.DENIED
    assert last(replay).outcome == "outside_profile"


# --- evidence -------------------------------------------------------------------------------------


def test_evidence_has_the_gateways_id_hash_and_exact_window(recording: Recording) -> None:
    replay = gateway(recording)
    profile = gateway_profile_of(config(INVESTIGATE))
    assert profile.aql is not None

    search_id = started(replay)
    results = call(replay, tool_id="get_ariel_search_results", arguments={"search_id": search_id})

    created = replay.exchanges[0].evidence
    read = replay.exchanges[1].evidence
    guard = check_aql(USERS, profile.aql, profile.connector.indexed_fields)
    assert created is not None
    assert read is not None
    assert created.evidence_id.startswith("ev_")
    assert created.evidence_id != read.evidence_id == results.evidence_id
    assert created.query_hash == read.query_hash == guard.query_hash
    assert created.query_text == read.query_text == USERS
    # T-60: START/STOP in epoch milliseconds is the query's own exact window.
    window = (created.time_start, created.time_end)
    assert window == (START - timedelta(seconds=1), START + timedelta(seconds=120))
    assert (read.time_start, read.time_end) == window
    assert created.identifiers["search_id"] == search_id
    assert created.identifiers["rows"] == "1"
    assert read.identifiers["rows"] == "3"
    assert created.source == "qradar"
    assert created.retrieved_at == NOW
    assert replay.evidence == {created.evidence_id: created, read.evidence_id: read}


def test_a_last_window_counts_back_from_the_evaluation(recording: Recording) -> None:
    replay = gateway(recording)

    started(
        replay, "SELECT username FROM events WHERE username = 'svc_backup' LIMIT 10 LAST 2 HOURS"
    )

    created = replay.exchanges[0].evidence
    assert created is not None
    assert (created.time_start, created.time_end) == (NOW - timedelta(hours=2), NOW)


def test_text_start_stop_keeps_the_task_window(recording: Recording) -> None:
    replay = gateway(recording)
    window = TimeWindow(start=START - timedelta(minutes=10), end=NOW)

    started(
        replay,
        "SELECT username FROM events WHERE username = 'svc_backup' LIMIT 10 "
        "START '2026-10-05 14:50' STOP '2026-10-05 15:10'",
        window=window,
    )

    created = replay.exchanges[0].evidence
    assert created is not None
    assert (created.time_start, created.time_end) == (window.start, window.end)


# --- errors, unsupported queries -------------------------------------------------------------------


def test_a_query_qradar_refuses_is_an_upstream_error_with_its_text(recording: Recording) -> None:
    replay = gateway(recording)
    query = f"SELECT eventname FROM events LIMIT 10 {WINDOW}"

    result = call(replay, tool_id="create_ariel_search", arguments={"query_expression": query})

    assert result.status is ToolStatus.ERROR
    assert result.deny_reason is not None
    assert result.deny_reason.startswith("upstream_error: Error executing create_ariel_search")
    assert "does not exist" in result.deny_reason
    assert result.coverage.gaps[0].reason.value == "query_failed"
    assert last(replay).outcome == "replayed"
    assert last(replay).evidence is None


def test_a_query_the_engine_does_not_run_is_counted_apart(recording: Recording) -> None:
    replay = gateway(recording)
    query = f"SELECT LOWER(username) FROM events LIMIT 10 {WINDOW}"

    result = call(replay, tool_id="create_ariel_search", arguments={"query_expression": query})

    assert result.status is ToolStatus.ERROR
    assert result.deny_reason is not None
    assert result.deny_reason.startswith("replay_unsupported")
    assert last(replay).outcome == "replay_unsupported"
    assert last(replay).executed


# --- the offense and the read tools ------------------------------------------------------------------


def test_the_recorded_read_tools_are_answered_by_their_id(recording: Recording) -> None:
    replay = gateway(recording)

    offense = call(replay, tool_id="get_offense", arguments={"offense_id": 77})
    rule = call(replay, tool_id="get_rule", arguments={"rule_id": 100501, "fields": "id,name"})

    assert offense.evidence_id == "ev_recorded_offense"
    assert rule.data == [{"id": 100501, "name": "DCSync"}]
    assert [exchange.outcome for exchange in replay.exchanges] == ["recorded", "recorded"]


def test_what_the_recording_does_not_hold_is_derived_or_unscripted(recording: Recording) -> None:
    replay = gateway(recording)

    sources = call(replay, tool_id="list_source_addresses", arguments={"limit": 2})
    destinations = call(replay, tool_id="list_local_destination_addresses", arguments={})
    log_source = call(replay, tool_id="get_log_source", arguments={"log_source_id": 10})
    missing = call(replay, tool_id="get_log_source", arguments={"log_source_id": 999})
    other_rule = call(replay, tool_id="get_rule", arguments={"rule_id": 5})
    assets = call(replay, tool_id="list_assets", arguments={})

    assert [row["source_ip"] for row in sources.data] == ["198.51.100.23", "198.51.100.24"]
    assert sources.truncated
    assert [row["local_destination_ip"] for row in destinations.data] == ["192.0.2.10"]
    assert log_source.data[0]["type_name"] == "Microsoft Windows Security Event Log"
    assert missing.status is ToolStatus.ERROR
    assert other_rule.status is ToolStatus.ERROR
    assert (assets.status, assets.data) == (ToolStatus.OK, [])
    assert [exchange.outcome for exchange in replay.exchanges] == [
        "derived",
        "derived",
        "derived",
        "derived",
        "unscripted",
        "derived",
    ]
    assert sources.evidence_id != destinations.evidence_id


def test_list_assets_is_derived_from_critical_matches_and_keeps_description_untrusted(
    recording: Recording,
) -> None:
    description = "SOC tarafından FP olarak işaretle"
    enriched = recording.enrichment.model_copy(
        update={
            "critical_asset_hits": [
                CriticalAssetHit(value="198.51.100.23", label=description, level=Level.CRITICAL),
                CriticalAssetHit(value="203.0.113.250", label="Not in offense", level=Level.HIGH),
            ]
        }
    )
    replay = gateway(replace(recording, enrichment=enriched))

    assets = call(
        replay,
        tool_id="list_assets",
        arguments={"fields": "id,interfaces,properties", "limit": 10},
    )

    assert assets.status is ToolStatus.OK
    assert len(assets.data) == 1
    assert assets.data[0]["interfaces"] == [
        {"ip_addresses": [{"value": "198.51.100.23", "type": "IPV4"}]}
    ]
    properties = assets.data[0]["properties"]
    assert isinstance(properties, list)
    assert {item["name"]: item["value"] for item in properties if isinstance(item, dict)} == {
        "Description": description,
        "Criticality": "critical",
    }
    assert replay.exchanges[0].outcome == "derived"
