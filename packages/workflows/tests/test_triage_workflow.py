"""TriageWorkflow: the run record around the agent, the wall clock budget, failures and why the
run failed (T-014 criterion 5), and replay.

The agent is ScriptedAgent, installed the way the worker installs the real one; its activities
stand for the model and tool activities TemporalDurability creates and have their names (the
real agent is covered in services/worker). Time is skipped on the Temporal test server.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from workflow_fakes import (
    AGENT_STEP,
    NO_USAGE,
    AgentReport,
    ScriptedAgent,
    TriageFakes,
    offense,
    triage_result,
)

from ais0c_contracts import (
    AgentTask,
    CatalogContext,
    EnrichmentContext,
    OffenseSnapshot,
    RunStatus,
    TriageResult,
)
from ais0c_workflows import (
    MODEL_ACCESS_FAILURES,
    GroupSummary,
    TriageFailure,
    TriageOutcome,
    TriageRequest,
    TriageWorkflow,
    agent_runtime,
)
from ais0c_workflows.names import (
    BEGIN_TRIAGE_RUN,
    CASE_TASK_QUEUE,
    FINISH_TRIAGE_RUN,
    TRIAGE_WORKFLOW,
)

pytestmark = pytest.mark.anyio

RUN_ID = "case-101-triage-1"


def request() -> TriageRequest:
    return TriageRequest(
        case_id="case-101",
        evaluation_no=1,
        parent_run_id="case-run-1",
        offense=offense(101, start=datetime(2026, 10, 3, 9, 30, tzinfo=UTC)),
        enrichment=EnrichmentContext(
            catalog=CatalogContext(rules=[], log_sources=[]),
            critical_asset_hits=[],
            ioc_hits=[],
            entity_resolutions=[],
        ),
    )


@pytest.fixture
def agent() -> ScriptedAgent:
    scripted = ScriptedAgent()
    agent_runtime.install_triage_agent(scripted)
    return scripted


@asynccontextmanager
async def running_triage(
    env: WorkflowEnvironment, fakes: TriageFakes
) -> AsyncIterator[WorkflowHandle[TriageWorkflow, TriageOutcome]]:
    async with Worker(
        env.client,
        task_queue=CASE_TASK_QUEUE,
        workflows=[TriageWorkflow],
        activities=fakes.activities(),
    ):
        yield await env.client.start_workflow(
            TriageWorkflow.run, request(), id=RUN_ID, task_queue=CASE_TASK_QUEUE
        )


async def test_a_run_is_recorded_before_and_after_the_agent(
    env: WorkflowEnvironment, agent: ScriptedAgent
) -> None:
    fakes = TriageFakes()

    async with running_triage(env, fakes) as handle:
        outcome = await handle.result()

    assert outcome == TriageOutcome(
        run_id=RUN_ID,
        status=RunStatus.COMPLETED,
        result=triage_result(),
        usage=triage_result().usage,
        error=None,
        failure=None,
    )
    # The run ID is the workflow ID; the request's case, evaluation and parent run go to the
    # run record, which exists before the agent makes its first call.
    assert fakes.begun == [(RUN_ID, "case-101", 1, "case-run-1", 101)]
    assert fakes.events.names() == ["step", "finished"]
    assert fakes.finished == [(RUN_ID, RunStatus.COMPLETED, triage_result(), None)]
    # The agent got the task and the nonce `begin_triage_run` returned, and its run ID from the
    # workflow (T-29).
    assert [task.task_id for task in agent.tasks] == [RUN_ID]
    assert agent.nonces == ["0123456789abcdef"]
    assert agent.run_ids == [RUN_ID]


async def test_the_wall_clock_budget_ends_the_run_as_budget_exhausted(
    env: WorkflowEnvironment, agent: ScriptedAgent
) -> None:

    async def model_slow_to_recover(run_id: str, attempt: int) -> TriageResult:
        """The model request fails and may be retried only after the budget has run out."""
        raise ApplicationError("LiteLLM answered 429", next_retry_delay=timedelta(minutes=10))

    fakes = TriageFakes(budget_seconds=180, step=model_slow_to_recover)
    async with running_triage(env, fakes) as handle:
        await fakes.events.wait_for("step")
        outcome = await handle.result()

    assert (outcome.status, outcome.result) == (RunStatus.BUDGET_EXHAUSTED, None)
    assert outcome.error == "the wall clock budget of 180 seconds ran out"
    # The model did not answer in time: a failure CaseWorkflow retries.
    assert outcome.failure is TriageFailure.TIMEOUT
    assert outcome.failure in MODEL_ACCESS_FAILURES
    assert outcome.usage.tokens == 0
    assert outcome.usage.seconds >= 180
    assert [(status, error) for _, status, _, error in fakes.finished] == [
        (RunStatus.BUDGET_EXHAUSTED, "the wall clock budget of 180 seconds ran out")
    ]


async def test_an_activity_that_fails_for_good_ends_the_run_as_failed(
    env: WorkflowEnvironment, agent: ScriptedAgent
) -> None:
    async def model_unreachable(run_id: str, attempt: int) -> TriageResult:
        raise ApplicationError("LiteLLM answered 503", type="ModelHTTPError", non_retryable=True)

    fakes = TriageFakes(step=model_unreachable)
    async with running_triage(env, fakes) as handle:
        outcome = await handle.result()

    assert (outcome.status, outcome.result) == (RunStatus.FAILED, None)
    assert outcome.error == "ModelHTTPError: LiteLLM answered 503"
    # The failed activity was a model request: a failure CaseWorkflow retries.
    assert outcome.failure is TriageFailure.MODEL_ERROR
    assert outcome.failure in MODEL_ACCESS_FAILURES
    assert [status for _, status, _, _ in fakes.finished] == [RunStatus.FAILED]


async def test_a_model_request_out_of_attempts_is_a_model_error(
    env: WorkflowEnvironment, agent: ScriptedAgent
) -> None:
    async def model_keeps_timing_out(run_id: str, attempt: int) -> TriageResult:
        raise ApplicationError("Request timed out.", type="APITimeoutError")

    fakes = TriageFakes(budget_seconds=3600, step=model_keeps_timing_out)
    async with running_triage(env, fakes) as handle:
        outcome = await handle.result()

    assert (outcome.status, outcome.failure) == (RunStatus.FAILED, TriageFailure.MODEL_ERROR)
    assert fakes.events.names().count("step") == 3


async def test_a_tool_call_that_fails_for_good_is_not_a_model_error(
    env: WorkflowEnvironment,
) -> None:
    """The gateway's outage is not the model's (architecture §13.3: fail closed)."""
    agent_runtime.install_triage_agent(ScriptedAgent(tool_call=True))

    async def gateway_unreachable(run_id: str, attempt: int) -> None:
        raise ApplicationError(
            "the gateway cannot be reached", type="GatewayUnavailableError", non_retryable=True
        )

    fakes = TriageFakes(tool=gateway_unreachable)
    async with running_triage(env, fakes) as handle:
        outcome = await handle.result()

    assert (outcome.status, outcome.failure) == (RunStatus.FAILED, TriageFailure.TOOL_ERROR)
    assert outcome.failure not in MODEL_ACCESS_FAILURES
    assert fakes.events.names() == ["tool", "finished"]


async def test_a_run_ending_without_a_result_is_recorded_as_such(
    env: WorkflowEnvironment,
) -> None:
    """An agent run that reports `budget_exhausted` itself (its token or call budget)."""

    async def exhausted(
        task: AgentTask,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        run_id: str,
        nonce: str,
        group_summary: GroupSummary | None = None,
    ) -> AgentReport:
        return AgentReport(
            status=RunStatus.BUDGET_EXHAUSTED,
            result=None,
            usage=NO_USAGE,
            error="UsageLimitExceeded: tool_calls_limit",
        )

    agent_runtime.install_triage_agent(exhausted)
    fakes = TriageFakes()
    async with running_triage(env, fakes) as handle:
        outcome = await handle.result()

    assert (outcome.status, outcome.error) == (
        RunStatus.BUDGET_EXHAUSTED,
        "UsageLimitExceeded: tool_calls_limit",
    )
    assert outcome.failure is TriageFailure.BUDGET_EXHAUSTED
    assert outcome.failure not in MODEL_ACCESS_FAILURES
    assert fakes.finished == [
        (RUN_ID, RunStatus.BUDGET_EXHAUSTED, None, "UsageLimitExceeded: tool_calls_limit")
    ]


async def test_a_run_the_agent_ends_failed_is_invalid_output(env: WorkflowEnvironment) -> None:
    """The agent catches what its model got wrong: output that kept failing validation."""

    async def invalid_output(
        task: AgentTask,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        run_id: str,
        nonce: str,
        group_summary: GroupSummary | None = None,
    ) -> AgentReport:
        return AgentReport(
            status=RunStatus.FAILED,
            result=None,
            usage=NO_USAGE,
            error="UnexpectedModelBehavior: Exceeded maximum retries (2) for output validation",
        )

    agent_runtime.install_triage_agent(invalid_output)
    fakes = TriageFakes()
    async with running_triage(env, fakes) as handle:
        outcome = await handle.result()

    assert (outcome.status, outcome.failure) == (RunStatus.FAILED, TriageFailure.INVALID_OUTPUT)
    assert outcome.failure not in MODEL_ACCESS_FAILURES


async def test_triage_history_replays(env: WorkflowEnvironment, agent: ScriptedAgent) -> None:
    fakes = TriageFakes()
    async with running_triage(env, fakes) as handle:
        await handle.result()
    history = await handle.fetch_history()

    replayer = Replayer(workflows=[TriageWorkflow], data_converter=pydantic_data_converter)
    await replayer.replay_workflow(history)


@workflow.defn(name=TRIAGE_WORKFLOW)
class TriageWithoutRecord:
    """TriageWorkflow as if the run were no longer recorded first: a nondeterministic change."""

    @workflow.run
    async def run(self, request: TriageRequest) -> None:
        await workflow.execute_activity(
            AGENT_STEP, "x", start_to_close_timeout=timedelta(seconds=30)
        )
        for name in (BEGIN_TRIAGE_RUN, FINISH_TRIAGE_RUN):
            await workflow.execute_activity(name, start_to_close_timeout=timedelta(seconds=30))


async def test_a_changed_triage_workflow_fails_the_replay(
    env: WorkflowEnvironment, agent: ScriptedAgent
) -> None:
    fakes = TriageFakes()
    async with running_triage(env, fakes) as handle:
        await handle.result()
    replayer = Replayer(
        workflows=[TriageWithoutRecord],
        data_converter=pydantic_data_converter,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    with pytest.raises(workflow.NondeterminismError):
        await replayer.replay_workflow(await handle.fetch_history())


def test_without_an_installed_agent_the_workflow_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_runtime, "_triage_agent", None)

    with pytest.raises(RuntimeError, match="no Triage agent is installed"):
        agent_runtime.triage_agent()
