"""TriageWorkflow: one run of the Triage agent for one evaluation of a case (architecture §7, §9,
§20).

CaseWorkflow starts it as a child with the ID `<case_id>-triage-<evaluation_no>`, which is also
the run's `agent_runs.run_id`. A run:

1. `begin_triage_run` records the run in `agent_runs` and returns its AgentTask and a fresh
   `untrusted_*` nonce. The gateway takes tool calls only for a recorded run in progress.
2. The Triage agent runs here, in workflow code, through Pydantic AI's TemporalDurability: each
   model request and each tool call is an activity of this workflow. A worker that stops in the
   middle loses nothing; the next worker replays the history and continues from the last
   completed activity.
3. `finish_triage_run` records how the run ended.

The manifest's wall clock budget bounds step 2. When it runs out, the run is abandoned and ends
`budget_exhausted`; a model or tool activity that keeps failing ends it `failed`. Either way the
workflow completes with the outcome, and CaseWorkflow decides what it means for the case.

RunStatus does not say why a run gave no decision, and CaseWorkflow retries only the runs the
model's outage ended (D-33), so the outcome also carries a `TriageFailure`.
"""

import asyncio
from datetime import timedelta
from enum import StrEnum
from typing import Final

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

from ais0c_workflows._activity import call
from ais0c_workflows.names import BEGIN_TRIAGE_RUN, FINISH_TRIAGE_RUN, TRIAGE_WORKFLOW

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict

    from ais0c_contracts import (
        AgentTask,
        EnrichmentContext,
        OffenseSnapshot,
        RunStatus,
        TriageResult,
        Usage,
    )
    from ais0c_workflows.agent_runtime import TriageRunReport, triage_agent

MAX_ERROR_LENGTH: Final = 1000
# Pydantic AI's TemporalDurability names an agent's model request activity
# `agent__<agent>__model_request` (`..._stream` when streamed). Its activity names are persisted
# compatibility data that do not change.
MODEL_REQUEST_ACTIVITIES: Final = ("__model_request", "__model_request_stream")


class TriageFailure(StrEnum):
    """Why a Triage run ended without a decision."""

    # A model request failed for good: its attempts ran out, it timed out or it was rejected.
    MODEL_ERROR = "model_error"
    # The wall clock budget ran out, for example because the model did not answer.
    TIMEOUT = "timeout"
    # Another activity of the agent failed for good, such as a tool call the gateway did not
    # answer.
    TOOL_ERROR = "tool_error"
    # The agent ended `failed` itself: the model kept breaking the output schema or the tool rules.
    INVALID_OUTPUT = "invalid_output"
    # The agent ended `budget_exhausted` itself: its token, tool call or step budget ran out.
    BUDGET_EXHAUSTED = "budget_exhausted"


# The failures of a model that cannot be reached: CaseWorkflow runs these once more (D-33). A
# model that does not answer ends the run on its wall clock budget before its own request times
# out (T-012 open question 5), so a timeout counts too.
MODEL_ACCESS_FAILURES: Final = frozenset({TriageFailure.MODEL_ERROR, TriageFailure.TIMEOUT})


class TriageRequest(BaseModel):
    """What CaseWorkflow hands to the Triage run of one evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    evaluation_no: int
    # The CaseWorkflow run that starts the triage: the AgentTask's parent run.
    parent_run_id: str
    offense: OffenseSnapshot
    enrichment: EnrichmentContext


class TriageOutcome(BaseModel):
    """How the Triage run ended; the workflow's result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: RunStatus
    # Set only when the status is `completed`.
    result: TriageResult | None
    usage: Usage
    # Why the run did not complete; for logs and traces, never shown to a model.
    error: str | None
    # Why the run gave no decision; None when it completed.
    failure: TriageFailure | None


@workflow.defn(name=TRIAGE_WORKFLOW)
class TriageWorkflow:
    @workflow.run
    async def run(self, request: TriageRequest) -> TriageOutcome:
        run_id = workflow.info().workflow_id
        task, nonce = await call(
            BEGIN_TRIAGE_RUN,
            run_id,
            request.case_id,
            request.evaluation_no,
            request.parent_run_id,
            request.offense,
            result_type=tuple[AgentTask, str],
        )
        outcome = await self._run_agent(run_id, task, nonce, request)
        await call(
            FINISH_TRIAGE_RUN,
            run_id,
            outcome.status,
            outcome.result,
            outcome.usage,
            outcome.error,
            result_type=type(None),
        )
        return outcome

    async def _run_agent(
        self, run_id: str, task: AgentTask, nonce: str, request: TriageRequest
    ) -> TriageOutcome:
        started = workflow.time()
        agent_run = asyncio.create_task(_report(task, request, nonce))
        budget = task.budget.seconds
        out_of_time = False
        try:
            await workflow.wait_condition(
                agent_run.done,
                timeout=timedelta(seconds=budget),
                timeout_summary="wall clock budget",
            )
        except TimeoutError:
            # The agent's activities are abandoned on cancellation, so nothing is asked of the
            # server; a request still running finishes on its worker and is ignored. The agent
            # sees the abandoned activity fail and stops.
            out_of_time = True
            agent_run.cancel()
            await workflow.wait_condition(agent_run.done)
        report: TriageRunReport | None = None
        failure: ActivityError | None = None
        if not agent_run.cancelled():
            try:
                report = agent_run.result()
            except ActivityError as error:
                failure = error
        if report is not None:
            return TriageOutcome(
                run_id=run_id,
                status=report.status,
                result=report.result,
                usage=report.usage,
                error=report.error,
                failure=_reported_failure(report.status),
            )
        seconds = workflow.time() - started
        if out_of_time:
            workflow.logger.warning("triage run %s ran out of its wall clock budget", run_id)
            return _without_result(
                run_id,
                RunStatus.BUDGET_EXHAUSTED,
                TriageFailure.TIMEOUT,
                f"the wall clock budget of {budget} seconds ran out",
                seconds=seconds,
            )
        if failure is None:
            # Cancelled from outside this method: the workflow itself is being cancelled.
            raise asyncio.CancelledError
        workflow.logger.warning("triage run %s failed: %s", run_id, _describe(failure))
        return _without_result(
            run_id, RunStatus.FAILED, _failed_activity(failure), _describe(failure), seconds=seconds
        )


async def _report(task: AgentTask, request: TriageRequest, nonce: str) -> TriageRunReport:
    return await triage_agent()(task, request.offense, request.enrichment, nonce=nonce)


def _without_result(
    run_id: str, status: RunStatus, failure: TriageFailure, error: str, *, seconds: float
) -> TriageOutcome:
    """An outcome of a run whose agent did not finish: its token and tool call counts are lost
    with it (the gateway's `tool_calls` rows still show the calls)."""
    return TriageOutcome(
        run_id=run_id,
        status=status,
        result=None,
        usage=Usage(tokens=0, tool_calls=0, seconds=max(0.0, seconds)),
        error=error[:MAX_ERROR_LENGTH],
        failure=failure,
    )


def _reported_failure(status: RunStatus) -> TriageFailure | None:
    """What a run status the agent reported itself means. Its model requests and tool calls are
    activities, so a failure the agent catches is the model's output or tool calls."""
    match status:
        case RunStatus.COMPLETED:
            return None
        case RunStatus.BUDGET_EXHAUSTED:
            return TriageFailure.BUDGET_EXHAUSTED
        case RunStatus.FAILED:
            return TriageFailure.INVALID_OUTPUT


def _failed_activity(error: ActivityError) -> TriageFailure:
    """The model request or the other activity of the agent that failed for good."""
    if (error.activity_type or "").endswith(MODEL_REQUEST_ACTIVITIES):
        return TriageFailure.MODEL_ERROR
    return TriageFailure.TOOL_ERROR


def _describe(error: ActivityError) -> str:
    """The failure of the activity that ended the run, e.g. a model request out of retries.

    An application error already names its type, e.g. `GatewayError: ...`.
    """
    cause = error.cause or error
    text = str(cause) if isinstance(cause, ApplicationError) else f"{type(cause).__name__}: {cause}"
    return text[:MAX_ERROR_LENGTH]
