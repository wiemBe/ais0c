"""TriageWorkflow: one run of the Triage agent for one evaluation of a case (architecture §7, §9,
§20).

CaseWorkflow starts it as a child with the ID `<case_id>-triage-<evaluation_no>`, which is also
the run's `agent_runs.run_id`. A run:

1. `begin_triage_run` records the run in `agent_runs` and returns its AgentTask and a fresh
   `untrusted_*` nonce. The gateway takes tool calls only for a recorded run in progress. In a
   group case (T-027) the request carries the group's summary, which the task's objective and
   the agent's prompt describe beside the snapshot of one of the group's offenses.
2. The Triage agent runs here, in workflow code, through Pydantic AI's TemporalDurability: each
   model request and each tool call is an activity of this workflow. A worker that stops in the
   middle loses nothing; the next worker replays the history and continues from the last
   completed activity.
3. `finish_triage_run` records how the run ended.

The manifest's wall clock budget bounds step 2 (`agent_run.run_within_budget`). When it runs
out, the run is abandoned and ends `budget_exhausted`; a model or tool activity that keeps
failing ends it `failed`. Either way the workflow completes with the outcome, and CaseWorkflow
decides what it means for the case. The outcome carries a `TriageFailure`, because CaseWorkflow
retries only the runs the model's outage ended (D-33).
"""

from temporalio import workflow

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
    from ais0c_workflows.agent_run import MODEL_ACCESS_FAILURES, AgentFailure, run_within_budget
    from ais0c_workflows.agent_runtime import triage_agent
    from ais0c_workflows.group_summary import GroupSummary

# Triage's name for AgentFailure: why a Triage run ended without a decision.
TriageFailure = AgentFailure

__all__ = [
    "MODEL_ACCESS_FAILURES",
    "TriageFailure",
    "TriageOutcome",
    "TriageRequest",
    "TriageWorkflow",
]


class TriageRequest(BaseModel):
    """What CaseWorkflow hands to the Triage run of one evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    evaluation_no: int
    # The CaseWorkflow run that starts the triage: the AgentTask's parent run.
    parent_run_id: str
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    # Set in a group case: the summary of the group `offense` belongs to (T-027).
    group_summary: GroupSummary | None = None


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
            request.group_summary,
            result_type=tuple[AgentTask, str],
        )
        end = await run_within_budget(
            lambda: triage_agent()(
                task,
                request.offense,
                request.enrichment,
                run_id=run_id,
                nonce=nonce,
                group_summary=request.group_summary,
            ),
            run_id=run_id,
            seconds=task.budget.seconds,
        )
        outcome = TriageOutcome(
            run_id=run_id,
            status=end.status,
            result=end.result,
            usage=end.usage,
            error=end.error,
            failure=end.failure,
        )
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
