"""AgentWorkflow: one run of a chain agent for one evaluation of a case (architecture §7, §9,
§20; T-026): the Orchestrator, Investigation, Verification or Reporting.

CaseWorkflow starts it as a child with the ID `<case_id>-<agent>-<evaluation_no>` (`-retry` for
the run that retries one the model's outage ended), which is also the run's `agent_runs.run_id`
(T-29). A run follows TriageWorkflow's pattern:

1. `begin_agent_run` records the run in `agent_runs`, with its model release and the skill it
   uses, and returns its AgentTask, a fresh `untrusted_*` nonce and that skill. A skill that
   fails its check (role, status, version, expiry, content hash) is left out: the step runs
   without one and the activity records why (T-21).
2. `load_evidence` reads the EvidenceRefs the task's `context_refs` name, the evidence of the
   claims and candidates the input carries.
3. The agent runs here, in workflow code, through TemporalDurability, within the task's wall
   clock budget (`agent_run.run_within_budget`).
4. `finish_agent_run` records how the run ended.

The workflow completes with the outcome whatever it is; CaseWorkflow decides what it means for
the case.
"""

from collections.abc import Awaitable, Sequence
from typing import Annotated, assert_never

from temporalio import workflow

from ais0c_workflows._activity import call
from ais0c_workflows.names import AGENT_WORKFLOW, BEGIN_AGENT_RUN, FINISH_AGENT_RUN, LOAD_EVIDENCE

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

    from ais0c_contracts import (
        AgentResult,
        AgentTask,
        Budget,
        CasePlan,
        CaseReport,
        EvidenceRef,
        InvestigationResult,
        RunStatus,
        SkillRef,
        TimeWindow,
        Usage,
        VerificationResult,
    )
    from ais0c_workflows.agent_run import AgentFailure, run_within_budget
    from ais0c_workflows.agent_runtime import (
        AgentKind,
        AgentRunReport,
        InvestigationInput,
        OrchestratorInput,
        ReportingInput,
        VerificationInput,
        investigation_agent,
        orchestrator_agent,
        reporting_agent,
        verification_agent,
    )


# The result of a chain agent run, whichever agent it is.
ChainResult = CasePlan | InvestigationResult | VerificationResult | CaseReport


class AgentRequest(BaseModel):
    """What CaseWorkflow hands to one chain agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    evaluation_no: int
    # The CaseWorkflow run that starts the agent: the AgentTask's parent run.
    parent_run_id: str
    # The task's objective: a plan step's, or the workflow's sentence for the Orchestrator and
    # Reporting.
    objective: str
    time_window: TimeWindow
    # A plan step's budget; None for the agent's manifest budget.
    budget: Budget | None
    # The skill a plan step chose; begin_agent_run checks it.
    skill: SkillRef | None
    # The task's context_refs: the evidence `inputs` cites.
    evidence_ids: tuple[str, ...]
    inputs: Annotated[
        OrchestratorInput | InvestigationInput | VerificationInput | ReportingInput,
        Field(discriminator="agent"),
    ]

    @property
    def agent(self) -> AgentKind:
        return self.inputs.agent


class AgentOutcome[ResultT: AgentResult](BaseModel):
    """How a chain agent run ended; the workflow's result.

    The workflow returns `AgentOutcome[ChainResult]`; CaseWorkflow reads it as the agent's own,
    e.g. `AgentOutcome[CasePlan]`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: RunStatus
    # Set only when the status is `completed`.
    result: SerializeAsAny[ResultT] | None
    usage: Usage
    # Why the run did not complete; for logs and traces, never shown to a model.
    error: str | None
    # Why the run gave no result; None when it completed.
    failure: AgentFailure | None


@workflow.defn(name=AGENT_WORKFLOW)
class AgentWorkflow:
    @workflow.run
    async def run(self, request: AgentRequest) -> AgentOutcome[ChainResult]:
        run_id = workflow.info().workflow_id
        task, nonce, skill = await call(
            BEGIN_AGENT_RUN,
            run_id,
            request.agent.value,
            request.case_id,
            request.parent_run_id,
            request.objective,
            request.time_window,
            request.budget,
            list(request.evidence_ids),
            request.skill,
            result_type=tuple[AgentTask, str, SkillRef | None],
        )
        evidence = (
            await call(LOAD_EVIDENCE, list(task.context_refs), result_type=list[EvidenceRef])
            if task.context_refs
            else []
        )
        end = await run_within_budget(
            lambda: _start(request, task, evidence, skill, run_id=run_id, nonce=nonce),
            run_id=run_id,
            seconds=task.budget.seconds,
        )
        await call(
            FINISH_AGENT_RUN,
            run_id,
            end.status,
            end.result,
            end.usage,
            end.error,
            result_type=type(None),
        )
        return AgentOutcome[ChainResult](
            run_id=run_id,
            status=end.status,
            result=end.result,
            usage=end.usage,
            error=end.error,
            failure=end.failure,
        )


def _start(
    request: AgentRequest,
    task: AgentTask,
    evidence: Sequence[EvidenceRef],
    skill: SkillRef | None,
    *,
    run_id: str,
    nonce: str,
) -> Awaitable[AgentRunReport[ChainResult]]:
    """Start the installed agent the request's input is for."""
    inputs = request.inputs
    match inputs:
        case OrchestratorInput():
            return orchestrator_agent()(
                task, inputs, evidence=evidence, skill=skill, run_id=run_id, nonce=nonce
            )
        case InvestigationInput():
            return investigation_agent()(
                task, inputs, evidence=evidence, skill=skill, run_id=run_id, nonce=nonce
            )
        case VerificationInput():
            return verification_agent()(
                task, inputs, evidence=evidence, skill=skill, run_id=run_id, nonce=nonce
            )
        case ReportingInput():
            return reporting_agent()(
                task, inputs, evidence=evidence, skill=skill, run_id=run_id, nonce=nonce
            )
        case _:
            assert_never(inputs)
