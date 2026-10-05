"""The Triage runtime and the run records: the agent's TemporalDurability activities, the run's
AgentTask, `begin_triage_run` and `finish_triage_run`, and the gateway client that binds each
tool call to its run (T-012 criteria 2 and 3)."""

import dataclasses
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from activity_payloads import offense, triage_result
from pydantic_ai.models.test import TestModel
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ais0c_activities import AgentRunGateway, SessionFactory, TriageRunActivities, TriageRuntime
from ais0c_agents import (
    FakeGatewayClient,
    GatewayError,
    ToolsetProfile,
    ToolSpec,
    load_manifest,
    load_model_registry,
    load_prompt,
)
from ais0c_agents.gateway_http import bound_run
from ais0c_contracts import (
    CostClass,
    RunStatus,
    TimeWindow,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    ToolStatus,
    Usage,
)
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
        prompt=load_prompt(REPO_ROOT, manifest.prompt),
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
    assert (task.agent_id, task.agent_version) == ("triage", "1.0.0")
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
        "1.0.0",
        "triage/v1",
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


class BindingGateway(FakeGatewayClient):
    def __init__(self) -> None:
        super().__init__(
            {
                "get_offense": ToolResult(
                    status=ToolStatus.OK,
                    evidence_id="ev_1",
                    data=[],
                    truncated=False,
                    coverage=ToolCoverage(complete=True, gaps=[]),
                )
            }
        )
        self.runs: list[str | None] = []

    async def call(self, intent: ToolIntent) -> ToolResult:
        self.runs.append(bound_run())
        return await super().call(intent)


def intent() -> ToolIntent:
    return ToolIntent(
        case_id="case-7",
        agent_id="triage",
        toolset_profile="qradar-triage-read",
        tool_id="get_offense",
        tool_schema_version="1",
        arguments={"offense_id": 7},
        reason="Read the offense.",
        expected_evidence="The offense record.",
        time_window=TimeWindow(start=NOW - timedelta(hours=1), end=NOW),
        cost_class=CostClass.LOW,
    )


async def test_a_tool_call_is_bound_to_the_run_of_its_workflow() -> None:
    """The tool activity's workflow is the Triage run, and its ID is the run's ID."""
    inner = BindingGateway()
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_id=RUN_ID)

    result = await env.run(AgentRunGateway(inner).call, intent())

    assert result.status is ToolStatus.OK
    assert inner.runs == [RUN_ID]
    assert bound_run() is None


async def test_a_call_outside_a_runs_activity_is_refused() -> None:
    inner = BindingGateway()
    no_workflow = ActivityEnvironment()
    no_workflow.info = dataclasses.replace(no_workflow.info, workflow_id=None)

    with pytest.raises(GatewayError, match="no workflow"):
        await no_workflow.run(AgentRunGateway(inner).call, intent())
    with pytest.raises(GatewayError, match="tool activity"):
        await AgentRunGateway(inner).call(intent())
    assert inner.intents == []
