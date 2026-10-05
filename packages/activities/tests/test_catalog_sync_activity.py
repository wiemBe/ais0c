"""sync_analysis_catalog: QRadar's inventory through the gateway into the catalog (T-022).

Criteria 1, 3, 5 and 6, and D-33's pseudo agent run. The gateway is the real one in process
with the repository's configuration, and the database is real (catalog_gateway.py). The fork
behind the gateway is replaced by `QRadarLists`; the lab test uses the fork itself.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from catalog_gateway import (
    CONFIG_DIR,
    INVENTORY_PROFILE,
    QRadarLists,
    Row,
    as_int,
    inventory_client,
    inventory_lists,
    log_source_rows,
    rule_rows,
    type_name,
    type_rows,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CATALOG_SYNC_AGENT_ID,
    KNOWLEDGE_SYNC_CONTEXT,
    CatalogSyncActivities,
    SessionFactory,
    SystemRunError,
)
from ais0c_activities.catalog import MIN_CALL_INTERVAL
from ais0c_agents import ToolsetProfile
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_contracts import CatalogMode, RunStatus, ToolStatus
from ais0c_mcp_gateway.registry import load_registry
from ais0c_storage import PolicyDecision
from ais0c_storage.models import AgentRunRow
from ais0c_storage.repositories import (
    list_agent_runs,
    list_catalog_log_sources,
    list_catalog_rules,
    list_tool_calls,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
# 160-character names: 200 rule rows are over the gateway's 32 KiB result limit.
LONG_NAME = 160


def lab_like(rules: int = 450, log_sources: int = 230, extra_types: int = 416) -> QRadarLists:
    """Longer lists than the gateway's 200-row pages; 419 types, as in the lab QRadar."""
    return QRadarLists(
        inventory_lists(
            rule_rows(rules, name_length=LONG_NAME),
            log_source_rows(log_sources),
            type_rows(extra_types),
        )
    )


def sync_activities(
    sessions: SessionFactory,
    gateway: HttpGatewayClient,
    profile: ToolsetProfile,
    *,
    sleep: Callable[[float], object] | None = None,
) -> CatalogSyncActivities:
    if sleep is None:
        return CatalogSyncActivities(
            sessions=sessions,
            gateway=gateway,
            profile=profile,
            clock=lambda: NOW,
            min_call_interval=timedelta(0),
        )

    async def record(seconds: float) -> None:
        sleep(seconds)

    return CatalogSyncActivities(
        sessions=sessions, gateway=gateway, profile=profile, clock=lambda: NOW, sleep=record
    )


async def sync_runs(sessions: SessionFactory) -> list[AgentRunRow]:
    async with sessions() as session:
        return await list_agent_runs(session, case_id=KNOWLEDGE_SYNC_CONTEXT)


async def catalog_ids(sessions: SessionFactory) -> tuple[list[int], list[int]]:
    async with sessions() as session:
        rules = await list_catalog_rules(session)
        sources = await list_catalog_log_sources(session)
    return [row.rule_id for row in rules], [row.log_source_id for row in sources]


def test_the_inventory_profile_reads_lists_and_has_no_ariel_search() -> None:
    """Criterion 1, in the configuration the gateway loads."""
    profile = load_registry(CONFIG_DIR, ["qradar"]).profiles[INVENTORY_PROFILE]

    assert set(profile.tools) == {
        "list_rules",
        "get_rule",
        "list_log_sources",
        "get_log_source",
        "list_log_source_types",
    }
    assert all(tool.search is None for tool in profile.tools.values())
    assert profile.aql is None
    # The instance with the read-only QRadar token (architecture §11.2).
    assert profile.instance == "qradar-mcp-read"


async def test_the_sync_reads_long_lists_in_full_and_adds_undefined_entries(
    sessions: SessionFactory,
) -> None:
    """Criteria 3 and 5: pages cut for size and for rows, and 419 types, read through the real
    gateway."""
    qradar = lab_like()
    gateway, profile = await inventory_client(sessions, qradar, now=lambda: NOW)

    counts = await sync_activities(sessions, gateway, profile).sync_analysis_catalog()

    assert counts == {
        "rules": 450,
        "rules_added": 450,
        "rules_renamed": 0,
        "rules_missing": 0,
        "log_sources": 230,
        "log_sources_added": 230,
        "log_sources_changed": 0,
        "log_sources_missing": 0,
        "log_sources_untyped": 0,
    }
    async with sessions() as session:
        rules = await list_catalog_rules(session)
        sources = await list_catalog_log_sources(session)
    assert [row.rule_id for row in rules] == list(range(100001, 100451))
    assert {(row.defined, row.mode, row.min_level) for row in rules} == {
        (False, CatalogMode.ANALYZE, None)
    }
    assert rules[0].rule_name == "AIS0C TEST - Rule 0000 ".ljust(LONG_NAME, "x")
    assert [row.log_source_id for row in sources] == list(range(2001, 2231))
    assert {(row.defined, row.in_scope) for row in sources} == {(False, True)}
    listed = {as_int(row["id"]): row for row in log_source_rows(230)}
    for row in sources:
        source = listed[row.log_source_id]
        assert (row.name, row.type_name) == (source["name"], type_name(as_int(source["type_id"])))

    # The gateway cut the rule pages for size, so they start at odd offsets; the other lists
    # came in pages of 200.
    rule_offsets = [as_int(arguments["offset"]) for arguments in qradar.calls_of("list_rules")]
    assert rule_offsets[0] == 0
    assert 0 < rule_offsets[1] < 200
    assert [arguments["offset"] for arguments in qradar.calls_of("list_log_sources")] == [0, 200]
    assert [arguments["offset"] for arguments in qradar.calls_of("list_log_source_types")] == [
        0,
        200,
        400,
    ]
    assert {as_int(arguments["limit"]) for _, arguments in qradar.calls} == {200}


async def test_the_reads_are_one_recorded_run_of_the_catalog_sync_pseudo_agent(
    sessions: SessionFactory,
) -> None:
    """D-33: the gateway records every call under a run of `catalog-sync`."""
    qradar = lab_like(rules=10, log_sources=5, extra_types=0)
    gateway, profile = await inventory_client(sessions, qradar, now=lambda: NOW)

    await sync_activities(sessions, gateway, profile).sync_analysis_catalog()

    [run] = await sync_runs(sessions)
    assert (run.agent_id, run.toolset_profile, run.status, run.tool_calls) == (
        CATALOG_SYNC_AGENT_ID,
        INVENTORY_PROFILE,
        RunStatus.COMPLETED,
        3,
    )
    assert (run.model_alias, run.prompt_version, run.started_at, run.ended_at) == (
        "none",
        "none",
        NOW,
        NOW,
    )
    async with sessions() as session:
        calls = await list_tool_calls(session, run.run_id)
    assert [call.intent.tool_id for call in calls] == [
        "list_rules",
        "list_log_sources",
        "list_log_source_types",
    ]
    assert {(call.policy_decision, call.status) for call in calls} == {
        (PolicyDecision.ALLOW, ToolStatus.OK)
    }
    assert all(call.evidence_id for call in calls)
    assert {(call.intent.case_id, call.intent.agent_id) for call in calls} == {
        (KNOWLEDGE_SYNC_CONTEXT, CATALOG_SYNC_AGENT_ID)
    }


async def test_a_second_run_changes_nothing(sessions: SessionFactory) -> None:
    """Criterion 6 through the activity: the second run reads QRadar again and writes
    nothing to the catalog."""
    qradar = lab_like(rules=250, log_sources=20, extra_types=0)
    gateway, profile = await inventory_client(sessions, qradar, now=lambda: NOW)
    activities = sync_activities(sessions, gateway, profile)
    first = await activities.sync_analysis_catalog()
    async with sessions() as session:
        before = [
            (row.rule_id, row.rule_name, row.updated_at)
            for row in await list_catalog_rules(session)
        ]

    second = await activities.sync_analysis_catalog()

    async with sessions() as session:
        after = [
            (row.rule_id, row.rule_name, row.updated_at)
            for row in await list_catalog_rules(session)
        ]
    assert after == before
    assert (first["rules_added"], first["log_sources_added"]) == (250, 20)
    assert second == {**first, "rules_added": 0, "log_sources_added": 0}
    assert [run.status for run in await sync_runs(sessions)] == [RunStatus.COMPLETED] * 2


async def test_a_failed_read_changes_nothing_and_is_retried(sessions: SessionFactory) -> None:
    qradar = lab_like(rules=10, log_sources=5, extra_types=0)
    qradar.failing.add("list_log_source_types")
    gateway, profile = await inventory_client(sessions, qradar, now=lambda: NOW)

    with pytest.raises(SystemRunError, match="list_log_source_types: error: upstream_error"):
        await sync_activities(sessions, gateway, profile).sync_analysis_catalog()

    assert await catalog_ids(sessions) == ([], [])
    [run] = await sync_runs(sessions)
    assert run.status is RunStatus.FAILED


async def test_unreadable_qradar_data_fails_for_good(sessions: SessionFactory) -> None:
    rules: list[Row] = [{"id": 100001, "name": None}]
    qradar = QRadarLists(inventory_lists(rules, log_source_rows(2), type_rows(0)))
    gateway, profile = await inventory_client(sessions, qradar, now=lambda: NOW)

    with pytest.raises(ApplicationError) as raised:
        await sync_activities(sessions, gateway, profile).sync_analysis_catalog()

    assert raised.value.type == "InventoryUnreadable"
    assert raised.value.non_retryable
    assert raised.value.message == "list_rules returned a row that cannot be read; check name"
    assert await catalog_ids(sessions) == ([], [])
    [run] = await sync_runs(sessions)
    assert run.status is RunStatus.FAILED


async def test_calls_are_spaced_and_each_one_heartbeats(sessions: SessionFactory) -> None:
    qradar = lab_like(rules=250, log_sources=5, extra_types=0)
    gateway, profile = await inventory_client(sessions, qradar, now=lambda: NOW)
    pauses: list[float] = []
    heartbeats: list[tuple[object, ...]] = []
    environment = ActivityEnvironment()
    environment.on_heartbeat = lambda *details: heartbeats.append(details)

    activities = sync_activities(sessions, gateway, profile, sleep=pauses.append)
    await environment.run(activities.sync_analysis_catalog)

    assert len(qradar.calls) == 4
    assert pauses == [MIN_CALL_INTERVAL.total_seconds()] * 3
    assert heartbeats == [
        ("list_rules",),
        ("list_rules",),
        ("list_log_sources",),
        ("list_log_source_types",),
        ("catalog",),
    ]
