"""What a case workflow does in each evaluation, shared by CaseWorkflow and GroupCaseWorkflow:
the agent chain (T-026) and the Action Executor's calls (T-045). Workflow code: both run in the
case workflow's own event loop and call activities and child workflows by name.

`AgentChain` runs one evaluation's chain:

1. Triage, the child workflow TriageWorkflow. Without a decision the chain stops.
2. The router lists the candidate skills of each plan agent (an activity), the Orchestrator
   plans, and `validate_plan` checks the plan or puts the default plan in its place (T-41). The
   reason a plan was replaced or a step dropped is recorded with the Orchestrator's run.
3. The plan's steps run in order: Investigation, if planned, and Verification.
4. Reporting writes the report of the decision.

Each agent after Triage is the child workflow AgentWorkflow; each gets the structured results of
the agents before it and never their free text (decision T-45). The decision is Investigation's
when it gave one and Triage's otherwise; its notification level is max(AI level, floor) (T-42).
A link that gives no result does not stop the chain: without a plan the default plan runs,
without Investigation Triage's decision stays, without Verification the case goes to operator
review (`verifier_conflict`), and without Reporting the decision is recorded without a report.
Any agent's run the model's outage ended (a model request that failed for good, or a run out of
its wall clock) is run once more after the configured wait (D-33).

In a group case (T-027) the chain evaluates the group: Triage gets the group's summary beside
the snapshot of one of its offenses, and the agents' objectives name the group. The agents
after Triage get the same structured results as in an offense case.

`ExecutorCalls` starts the executor's note and e-mail calls of a case on the `soc-executor`
queue, beside the case (T-59 (1)): each call runs after the earlier calls of the same activity,
so an offense's notes reach QRadar in order, and the case waits for them only when it closes or
continues as new. A failure that stays after the retries is logged and, for the platform's own records, written
down: the case queue's `record_executor_failure` stores the note or e-mail as `failed` with the
error `executor_unavailable` (T-032, T-59 (7)), so the write-failure alarm and the analyst UI
see it. Nothing else of the case changes.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import (
    ActivityError,
    CancelledError,
    ChildWorkflowError,
    WorkflowAlreadyStartedError,
)

from ais0c_workflows._activity import call
from ais0c_workflows.agent import AgentOutcome, AgentRequest
from ais0c_workflows.names import (
    AGENT_RETRY_DELAY,
    AGENT_WORKFLOW,
    CANDIDATE_SKILLS,
    CASE_URL,
    EVALUATION_WINDOW,
    EXECUTOR_TASK_QUEUE,
    PLAN_BUDGETS,
    RECORD_EXECUTOR_FAILURE,
    RECORD_PLAN,
    SKILL_TELEMETRY,
    TRIAGE_WORKFLOW,
    agent_workflow_id,
    triage_workflow_id,
)
from ais0c_workflows.triage import MODEL_ACCESS_FAILURES, TriageOutcome, TriageRequest

with workflow.unsafe.imports_passed_through():
    from ais0c_contracts import (
        AgentResult,
        Budget,
        CasePlan,
        CaseReport,
        EnrichmentContext,
        InvestigationResult,
        OffenseSnapshot,
        PlanStep,
        RunStatus,
        SkillRef,
        TimeWindow,
        TriageResult,
        VerificationResult,
    )
    from ais0c_workflows.agent_runtime import (
        AgentKind,
        InvestigationInput,
        OrchestratorInput,
        ReportingInput,
        TelemetrySource,
        VerificationInput,
    )
    from ais0c_workflows.chain import (
        ChainDecision,
        case_data_gaps,
        claims_are_critical,
        decision_of,
        evidence_ids,
        notify_level,
        qa_reasons,
        undisputed_claims,
    )
    from ais0c_workflows.group_summary import GroupSummary
    from ais0c_workflows.notify import (
        EXECUTOR_ATTEMPT_TIMEOUT,
        EXECUTOR_RETRY,
        EXECUTOR_TOTAL_TIMEOUT,
        CaseAlertRequest,
        EvaluationNoteRequest,
        GroupAlertRequest,
        NoDecisionNoteRequest,
        abandoned_call,
    )
    from ais0c_workflows.plan import (
        INVESTIGATION,
        VERIFICATION,
        PlanCandidate,
        PlanDecision,
        validate_plan,
    )

# Writing a given-up call down is one database insert on the case queue.
RECORD_FAILURE_TIMEOUT = timedelta(minutes=10)

type ExecutorRequest = (
    EvaluationNoteRequest | NoDecisionNoteRequest | CaseAlertRequest | GroupAlertRequest
)


def subject_of(offense: OffenseSnapshot, group_summary: GroupSummary | None) -> str:
    """What the chain agents' objectives say is evaluated: the offense, or the group."""
    if group_summary is None:
        return f"QRadar offense {offense.offense_id}"
    return (
        f"the group of {group_summary.offense_count} QRadar offenses of the same rules that "
        f"offense {offense.offense_id} belongs to"
    )


class AgentChain:
    """The agent chain of a case's evaluations; `case_id` is the case workflow's ID."""

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id

    async def run(
        self,
        evaluation_no: int,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        group_summary: GroupSummary | None = None,
    ) -> ChainDecision | None:
        """The evaluation's agent chain; None when Triage gives no decision."""
        triage = await self._triage(evaluation_no, offense, enrichment, group_summary)
        if triage is None:
            return None
        subject = subject_of(offense, group_summary)
        window = await call(EVALUATION_WINDOW, offense, result_type=TimeWindow)
        candidates = await self._candidates(offense, enrichment)
        plan, planned = await self._plan(
            evaluation_no, offense, triage, subject=subject, window=window, candidates=candidates
        )
        investigation: InvestigationResult | None = None
        verification: VerificationResult | None = None
        for step in plan.steps:
            decision = decision_of(triage, investigation)
            if step.agent_id == INVESTIGATION:
                skill = _skill_of(step, candidates)
                telemetry: tuple[TelemetrySource, ...] | None = None
                if skill is not None:
                    resolved = await call(
                        SKILL_TELEMETRY,
                        skill.skill_id,
                        skill.version,
                        result_type=list[TelemetrySource],
                    )
                    telemetry = tuple(resolved)
                inputs = InvestigationInput(
                    offense=offense,
                    enrichment=enrichment,
                    verdict=triage.verdict,
                    confidence=triage.confidence,
                    ai_level=triage.ai_level,
                    investigation_focus=tuple(triage.investigation_focus),
                    claims=tuple(triage.claims),
                    data_gaps=tuple(triage.data_gaps),
                    telemetry=telemetry,
                )
                investigation = await self._agent(
                    self._step_request(
                        evaluation_no, step, candidates, inputs, evidence_ids(triage.claims)
                    ),
                    AgentOutcome[InvestigationResult],
                )
            elif step.agent_id == VERIFICATION:
                level = notify_level(decision.ai_level, enrichment.floor_level)
                inputs = VerificationInput(
                    offense=offense,
                    verdict=decision.verdict,
                    confidence=decision.confidence,
                    ai_level=decision.ai_level,
                    claims=decision.claims,
                    critical=claims_are_critical(decision.verdict, level),
                )
                verification = await self._agent(
                    self._step_request(
                        evaluation_no, step, candidates, inputs, evidence_ids(decision.claims)
                    ),
                    AgentOutcome[VerificationResult],
                )
        decision = decision_of(triage, investigation)
        level = notify_level(decision.ai_level, enrichment.floor_level)
        gaps = case_data_gaps(decision, verification)
        claims = undisputed_claims(decision.claims, verification)
        urgent = () if investigation is None else tuple(investigation.urgent_event_candidates)
        report = await self._agent(
            AgentRequest(
                case_id=self._case_id,
                evaluation_no=evaluation_no,
                parent_run_id=workflow.info().run_id,
                objective=f"Write the report of {subject} (evaluation {evaluation_no}).",
                time_window=window,
                budget=None,
                skill=None,
                evidence_ids=evidence_ids(claims, urgent),
                inputs=ReportingInput(
                    offense=offense,
                    enrichment=enrichment,
                    verdict=decision.verdict,
                    confidence=decision.confidence,
                    notify_level=level,
                    claims=claims,
                    urgent_event_candidates=urgent,
                    data_gaps=gaps,
                ),
            ),
            AgentOutcome[CaseReport],
        )
        results: list[AgentResult | None] = [triage, planned, investigation, verification, report]
        return ChainDecision(
            verdict=decision.verdict,
            confidence=decision.confidence,
            ai_level=decision.ai_level,
            notify_level=level,
            report=report,
            qa_reasons=qa_reasons(
                decision,
                verification=verification,
                injection_suspected=any(r.injection_suspected for r in results if r is not None),
                data_gaps=gaps,
            ),
        )

    async def _candidates(
        self, offense: OffenseSnapshot, enrichment: EnrichmentContext
    ) -> tuple[PlanCandidate, ...]:
        """The router's candidate skills of each plan agent, as (agent, skill, its budget)."""
        listed = await call(
            CANDIDATE_SKILLS,
            offense,
            enrichment,
            result_type=list[tuple[str, SkillRef, Budget]],
        )
        return tuple(
            PlanCandidate(agent_id=agent, skill=skill, budget=budget)
            for agent, skill, budget in listed
        )

    async def _plan(
        self,
        evaluation_no: int,
        offense: OffenseSnapshot,
        triage: TriageResult,
        *,
        subject: str,
        window: TimeWindow,
        candidates: tuple[PlanCandidate, ...],
    ) -> tuple[PlanDecision, CasePlan | None]:
        """The validated plan, and the Orchestrator's plan as it proposed it (None without
        one)."""
        plan_budget, agents = await call(PLAN_BUDGETS, result_type=tuple[Budget, dict[str, Budget]])
        request = AgentRequest(
            case_id=self._case_id,
            evaluation_no=evaluation_no,
            parent_run_id=workflow.info().run_id,
            objective=f"Plan the rest of the evaluation of {subject} (evaluation {evaluation_no}).",
            time_window=window,
            budget=None,
            skill=None,
            evidence_ids=(),
            inputs=OrchestratorInput(
                offense=offense,
                verdict=triage.verdict,
                confidence=triage.confidence,
                ai_level=triage.ai_level,
                needs_investigation=triage.needs_investigation,
                investigation_focus=tuple(triage.investigation_focus),
                data_gaps=tuple(triage.data_gaps),
                injection_suspected=triage.injection_suspected,
                candidates=candidates,
                agents=agents,
                plan_budget=plan_budget,
            ),
        )
        outcome = await self._agent_outcome(request, AgentOutcome[CasePlan])
        planned = _completed(outcome)
        plan = validate_plan(
            planned,
            agents=agents,
            candidates=candidates,
            plan_budget=plan_budget,
            window=window,
            needs_investigation=triage.needs_investigation,
        )
        if plan.rejection is not None or plan.dropped:
            rejection = plan.rejection
            await call(
                RECORD_PLAN,
                self._case_id,
                agent_workflow_id(self._case_id, AgentKind.ORCHESTRATOR, evaluation_no)
                if outcome is None
                else outcome.run_id,
                None if rejection is None else rejection.reason.value,
                None if rejection is None else rejection.step,
                None if rejection is None else rejection.detail,
                list(plan.dropped),
                [step.agent_id for step in plan.steps],
                result_type=type(None),
            )
        return plan, planned

    def _step_request(
        self,
        evaluation_no: int,
        step: PlanStep,
        candidates: tuple[PlanCandidate, ...],
        inputs: InvestigationInput | VerificationInput,
        cited: tuple[str, ...],
    ) -> AgentRequest:
        """The request of a plan step: its objective, window, budget and skill (T-48: the
        objective is the agent's AgentTask.objective and goes nowhere else)."""
        return AgentRequest(
            case_id=self._case_id,
            evaluation_no=evaluation_no,
            parent_run_id=workflow.info().run_id,
            objective=step.objective,
            time_window=step.time_window,
            budget=step.budget,
            skill=_skill_of(step, candidates),
            evidence_ids=cited,
            inputs=inputs,
        )

    async def _agent[ResultT: AgentResult](
        self, request: AgentRequest, outcome_type: type[AgentOutcome[ResultT]]
    ) -> ResultT | None:
        """The result of the agent `request` is for; None when it gives none."""
        return _completed(await self._agent_outcome(request, outcome_type))

    async def _agent_outcome[ResultT: AgentResult](
        self, request: AgentRequest, outcome_type: type[AgentOutcome[ResultT]]
    ) -> AgentOutcome[ResultT] | None:
        """One chain agent run, retried once after the wait if the model's outage ended it
        (D-33); None when its workflow failed."""
        outcome = await self._agent_run(request, outcome_type, retry=False)
        if outcome is not None and outcome.failure in MODEL_ACCESS_FAILURES:
            await self._retry_wait(outcome.run_id, str(outcome.failure))
            outcome = await self._agent_run(request, outcome_type, retry=True)
        return outcome

    async def _agent_run[ResultT: AgentResult](
        self,
        request: AgentRequest,
        outcome_type: type[AgentOutcome[ResultT]],
        *,
        retry: bool,
    ) -> AgentOutcome[ResultT] | None:
        run_id = agent_workflow_id(
            request.case_id, request.agent, request.evaluation_no, retry=retry
        )
        outcome = await self._child(AGENT_WORKFLOW, request, run_id, outcome_type)
        if outcome is not None and outcome.result is None:
            workflow.logger.warning(
                "%s run %s ended %s (%s): %s",
                request.agent,
                run_id,
                outcome.status,
                outcome.failure,
                outcome.error,
            )
        return outcome

    async def _triage(
        self,
        evaluation_no: int,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        group_summary: GroupSummary | None,
    ) -> TriageResult | None:
        """Run the Triage agent for this evaluation; None when it gives no decision.

        A run the model's outage ended is run once more after the configured wait (D-33).
        """
        request = TriageRequest(
            case_id=self._case_id,
            evaluation_no=evaluation_no,
            parent_run_id=workflow.info().run_id,
            offense=offense,
            enrichment=enrichment,
            group_summary=group_summary,
        )
        outcome = await self._triage_run(request, retry=False)
        if outcome is not None and outcome.failure in MODEL_ACCESS_FAILURES:
            await self._retry_wait(outcome.run_id, str(outcome.failure))
            outcome = await self._triage_run(request, retry=True)
        if outcome is None or outcome.status is not RunStatus.COMPLETED:
            return None
        return outcome.result

    async def _triage_run(self, request: TriageRequest, *, retry: bool) -> TriageOutcome | None:
        """One Triage run; None when its workflow failed."""
        run_id = triage_workflow_id(self._case_id, request.evaluation_no, retry=retry)
        outcome = await self._child(TRIAGE_WORKFLOW, request, run_id, TriageOutcome)
        if outcome is not None and (
            outcome.status is not RunStatus.COMPLETED or outcome.result is None
        ):
            workflow.logger.warning(
                "triage run %s ended %s (%s): %s",
                run_id,
                outcome.status,
                outcome.failure,
                outcome.error,
            )
        return outcome

    async def _child[OutcomeT](
        self, workflow_name: str, request: object, run_id: str, outcome_type: type[OutcomeT]
    ) -> OutcomeT | None:
        """One agent run as a child workflow; None when its workflow failed.

        The child keeps its many activities out of this history. Closing the case abandons the
        child instead of cancelling it: the run finishes on its own and records itself, and no
        cancel request can cross its completion. The chain stops there: the abandoned wait
        raises CancelledError. The child's ID is never reused, so an evaluation runs an agent at
        most once, and retries it at most once.
        """
        try:
            return await workflow.execute_child_workflow(
                workflow_name,
                request,
                id=run_id,
                result_type=outcome_type,
                cancellation_type=workflow.ChildWorkflowCancellationType.ABANDON,
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except ChildWorkflowError as error:
            if isinstance(error.cause, CancelledError):
                raise asyncio.CancelledError from error
            workflow.logger.warning("agent run %s failed: %s", run_id, error)
            return None
        except WorkflowAlreadyStartedError as error:
            workflow.logger.warning("agent run %s failed: %s", run_id, error)
            return None

    async def _retry_wait(self, run_id: str, failure: str) -> None:
        delay = await call(AGENT_RETRY_DELAY, result_type=timedelta)
        workflow.logger.warning("agent run %s is retried in %s: %s", run_id, delay, failure)
        await workflow.sleep(delay, summary="agent retry")


class ExecutorCalls:
    """The executor's note and e-mail calls of a case; `case_id` is the case workflow's ID."""

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id
        # The calls in progress: the last one of each activity.
        self._writes: dict[str, asyncio.Task[None]] = {}
        self._case_url: str | None = None

    async def case_link(self) -> str:
        """The case's page on the platform, from the worker's setting (T-045 criterion 6)."""
        if self._case_url is None:
            self._case_url = await call(CASE_URL, self._case_id, result_type=str)
        return self._case_url

    def write(self, name: str, request: ExecutorRequest) -> None:
        """Start one executor call beside the case, after the earlier calls of activity `name`:
        an offense's notes reach QRadar in the order the case made them, and a note that is
        retried holds up neither the case nor the e-mail."""
        self._writes[name] = asyncio.create_task(
            self._executor_call(name, request, after=self._writes.get(name))
        )

    async def _executor_call(
        self, name: str, request: ExecutorRequest, *, after: asyncio.Task[None] | None
    ) -> None:
        """One call to an executor activity on the `soc-executor` queue.

        The outcome is the executor's own record (`notes_written`, `notifications`), so the
        workflow does not read it; with writes off it is `disabled` (T-23). A failure the
        retries did not get past is logged and changes nothing else (T-045 criterion 8).
        """
        if after is not None:
            await after
        try:
            await call(
                name,
                request,
                result_type=dict[str, object],
                attempt_timeout=EXECUTOR_ATTEMPT_TIMEOUT,
                total_timeout=EXECUTOR_TOTAL_TIMEOUT,
                retry_policy=EXECUTOR_RETRY,
                task_queue=EXECUTOR_TASK_QUEUE,
            )
        except ActivityError as error:
            workflow.logger.warning("%s of case %s failed: %s", name, self._case_id, error)
            await self._record_failure(name, request)

    async def _record_failure(self, name: str, request: ExecutorRequest) -> None:
        """Write the given-up call down as `failed`/`executor_unavailable` (T-032); a row the
        executor made itself stays as it is. A failure of this is logged too."""
        try:
            await call(
                RECORD_EXECUTOR_FAILURE,
                abandoned_call(request),
                result_type=bool,
                total_timeout=RECORD_FAILURE_TIMEOUT,
            )
        except ActivityError as error:
            workflow.logger.warning(
                "the failure of %s of case %s could not be recorded: %s", name, self._case_id, error
            )

    def writing(self) -> bool:
        return any(not task.done() for task in self._writes.values())

    async def done(self) -> None:
        """Wait for the calls in progress, each at most EXECUTOR_TOTAL_TIMEOUT."""
        for task in list(self._writes.values()):
            await task


def _completed[ResultT: AgentResult](outcome: AgentOutcome[ResultT] | None) -> ResultT | None:
    if outcome is None or outcome.status is not RunStatus.COMPLETED:
        return None
    return outcome.result


def _skill_of(step: PlanStep, candidates: tuple[PlanCandidate, ...]) -> SkillRef | None:
    """The step's skill with its content hash; validate_plan let only a candidate through."""
    if not step.skill_id:
        return None
    for candidate in candidates:
        skill = candidate.skill
        if (candidate.agent_id, skill.skill_id, skill.version) == (
            step.agent_id,
            step.skill_id,
            step.skill_version,
        ):
            return skill
    return None
