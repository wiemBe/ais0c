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
"""

import asyncio
from datetime import timedelta
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
            )
        seconds = workflow.time() - started
        if out_of_time:
            workflow.logger.warning("triage run %s ran out of its wall clock budget", run_id)
            return _without_result(
                run_id,
                RunStatus.BUDGET_EXHAUSTED,
                f"the wall clock budget of {budget} seconds ran out",
                seconds=seconds,
            )
        if failure is None:
            # Cancelled from outside this method: the workflow itself is being cancelled.
            raise asyncio.CancelledError
        workflow.logger.warning("triage run %s failed: %s", run_id, _describe(failure))
        return _without_result(run_id, RunStatus.FAILED, _describe(failure), seconds=seconds)


async def _report(task: AgentTask, request: TriageRequest, nonce: str) -> TriageRunReport:
    return await triage_agent()(task, request.offense, request.enrichment, nonce=nonce)


def _without_result(run_id: str, status: RunStatus, error: str, *, seconds: float) -> TriageOutcome:
    """An outcome of a run whose agent did not finish: its token and tool call counts are lost
    with it (the gateway's `tool_calls` rows still show the calls)."""
    return TriageOutcome(
        run_id=run_id,
        status=status,
        result=None,
        usage=Usage(tokens=0, tool_calls=0, seconds=max(0.0, seconds)),
        error=error[:MAX_ERROR_LENGTH],
    )


def _describe(error: ActivityError) -> str:
    """The failure of the activity that ended the run, e.g. a model request out of retries.

    An application error already names its type, e.g. `GatewayError: ...`.
    """
    cause = error.cause or error
    text = str(cause) if isinstance(cause, ApplicationError) else f"{type(cause).__name__}: {cause}"
    return text[:MAX_ERROR_LENGTH]
