"""The Triage runtime and the run records: the agent's TemporalDurability activities, the run's
AgentTask, `begin_triage_run` and `finish_triage_run` (T-012 criteria 2 and 3)."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from activity_payloads import offense, triage_result
from pydantic_ai.models.test import TestModel
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

import ais0c_activities.gateway
from ais0c_activities import SessionFactory, TriageRunActivities, TriageRuntime
from ais0c_agents import (
    FakeGatewayClient,
    ToolsetProfile,
    ToolSpec,
    load_agent_prompt,
    load_manifest,
    load_model_registry,
)
from ais0c_contracts import CostClass, RunStatus, TimeWindow, Usage
from ais0c_storage.repositories import get_agent_run

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RUN_ID = "case-7-triage-1"

PROFILE = ToolsetProfile(
    name="qradar-triage-read",
    connector="qradar",
    tools=(
        ToolSpec(
            id="get_offense",
            description="Read one offense.",
            schema_version="1",
            cost_class=CostClass.LOW,
            parameters={"type": "object", "properties": {"offense_id": {"type": "integer"}}},
        ),
    ),
)


def runtime(gateway: FakeGatewayClient | None = None) -> TriageRuntime:
    manifest = load_manifest(
        REPO_ROOT / "config/agents/triage.yaml",
        load_model_registry(REPO_ROOT / "config/models/registry.dev.yaml"),
    )
    return TriageRuntime.build(
        manifest=manifest,
        prompt=load_agent_prompt(REPO_ROOT, manifest),
        profile=PROFILE,
        gateway=gateway or FakeGatewayClient(),
        model=TestModel(),
        model_target="test-target",
    )


@pytest.fixture
def runs(sessions: SessionFactory) -> TriageRunActivities:
    return TriageRunActivities(sessions=sessions, runtime=runtime(), clock=lambda: NOW)


def test_the_agent_brings_its_model_and_tool_activities() -> None:
    """Criterion 2: TemporalDurability registers the agent's model requests and its gateway
    tools as activities, named after the agent and the toolset."""
    names = {
        getattr(fn, "__temporal_activity_definition").name for fn in runtime().temporal_activities
    }

    assert {
        "agent__triage__model_request",
        "agent__triage__toolset__gateway-qradar-triage-read__call_tool",
    } <= names


def test_the_task_carries_the_manifests_budget_and_the_offenses_window() -> None:
    triage = runtime()
    task = triage.task(
        run_id=RUN_ID,
        case_id="case-7",
        evaluation_no=1,
        parent_run_id="case-run-1",
        offense=offense(7, start=NOW - timedelta(hours=2)),
        now=NOW,
    )

    assert (task.task_id, task.case_id, task.hunt_id, task.parent_run_id) == (
        RUN_ID,
        "case-7",
        None,
        "case-run-1",
    )
    assert (task.agent_id, task.agent_version) == ("triage", "1.1.0")
    assert task.objective == "Triage QRadar offense 7 (evaluation 1)."
    assert task.time_window == TimeWindow(start=NOW - timedelta(hours=2), end=NOW)
    assert (task.budget.tokens, task.budget.tool_calls, task.budget.seconds) == (150000, 12, 420)


@pytest.mark.parametrize(
    ("start", "window_start"),
    [
        (NOW - timedelta(days=90), NOW - timedelta(days=30)),
        (NOW + timedelta(minutes=3), NOW - timedelta(minutes=1)),
    ],
    ids=["old offense", "clock skew"],
)
def test_the_window_stays_within_the_profile(start: datetime, window_start: datetime) -> None:
    task = runtime().task(
        run_id=RUN_ID,
        case_id="case-7",
        evaluation_no=1,
        parent_run_id="case-run-1",
        offense=offense(7, start=start),
        now=NOW,
    )

    assert task.time_window == TimeWindow(start=window_start, end=NOW)


async def test_begin_records_the_run_before_the_agent_calls_the_gateway(
    runs: TriageRunActivities, sessions: SessionFactory
) -> None:
    env = ActivityEnvironment()

    task, nonce = await env.run(
        runs.begin_triage_run, RUN_ID, "case-7", 1, "case-run-1", offense(7, start=NOW)
    )

    async with sessions() as session:
        row = await get_agent_run(session, RUN_ID)
    assert row is not None
    assert (row.case_id, row.agent_id, row.agent_version, row.prompt_version) == (
        "case-7",
        "triage",
        "1.1.0",
        "triage/v2",
    )
    assert (row.model_alias, row.model_target, row.toolset_profile) == (
        "soc-fast",
        "test-target",
        "qradar-triage-read",
    )
    # In progress: the gateway accepts calls for it.
    assert (row.status, row.ended_at, row.started_at) == (None, None, NOW)
    assert row.task == task
    assert re.fullmatch(r"[0-9a-f]{8,64}", nonce)


async def test_a_retried_begin_returns_the_recorded_task_with_a_fresh_nonce(
    runs: TriageRunActivities,
) -> None:
    env = ActivityEnvironment()
    snapshot = offense(7, start=NOW)

    first = await env.run(runs.begin_triage_run, RUN_ID, "case-7", 1, "case-run-1", snapshot)
    again = await env.run(runs.begin_triage_run, RUN_ID, "case-7", 1, "case-run-1", snapshot)

    assert first[0] == again[0]
    assert first[1] != again[1]


async def test_a_run_that_ended_cannot_begin_again(runs: TriageRunActivities) -> None:
    env = ActivityEnvironment()
    await env.run(runs.begin_triage_run, RUN_ID, "case-7", 1, "case-run-1", offense(7, start=NOW))
    await env.run(
        runs.finish_triage_run,
        RUN_ID,
        RunStatus.FAILED,
        None,
        Usage(tokens=0, tool_calls=0, seconds=1.0),
        "boom",
    )

    with pytest.raises(ApplicationError) as error:
        await env.run(
            runs.begin_triage_run, RUN_ID, "case-7", 1, "case-run-1", offense(7, start=NOW)
        )
    assert (error.value.type, error.value.non_retryable) == ("AgentRunConflict", True)


async def test_finish_records_the_triage_result(
    runs: TriageRunActivities, sessions: SessionFactory
) -> None:
    env = ActivityEnvironment()
    await env.run(runs.begin_triage_run, RUN_ID, "case-7", 1, "case-run-1", offense(7, start=NOW))
    result = triage_result()
    usage = Usage(tokens=1234, tool_calls=2, seconds=12.5)

    await env.run(runs.finish_triage_run, RUN_ID, RunStatus.COMPLETED, result, usage, None)

    async with sessions() as session:
        row = await get_agent_run(session, RUN_ID)
    assert row is not None
    assert (row.status, row.result, row.tokens, row.tool_calls, row.ended_at) == (
        RunStatus.COMPLETED,
        result,
        1234,
        2,
        NOW,
    )


def test_tool_calls_name_their_run_without_a_client_wrapper() -> None:
    """T-013: the agent writes the run's ID from its deps into every ToolIntent, so the agent
    gets the gateway client itself; the client that bound calls to the tool activity's workflow
    is gone. The worker tests check the ID of a run under Temporal."""
    assert not hasattr(ais0c_activities, "AgentRunGateway")
    assert not hasattr(ais0c_activities.gateway, "AgentRunGateway")
