"""CaseWorkflow: the case of one offense, from its first evaluation until QRadar closes it
(architecture §6, §9, §20).

OffenseIntake starts it with the ID `case-<offense_id>`. An evaluation fetches the offense,
enriches it, opens the case (or starts the next evaluation), runs the agent chain and records the
decision. The SLA timer covers the whole chain: when the deadline passes first the case becomes
`no_ai_decision`, so the operator knows the AI has not looked at it. The chain keeps running and
a later decision still replaces that status (D-30).

The chain of one evaluation (T-026):

1. Triage, the child workflow TriageWorkflow. Without a decision the chain stops and the case is
   `no_ai_decision` until an update is evaluated.
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

After the decision is recorded, the Action Executor writes the evaluation's QRadar note, and its
alert e-mail when the level is high or critical (D-18, D-22, T-045). An evaluation without a
decision gets the "AI değerlendirmesi yapılamadı" note as soon as it is without one, and a
decision that arrives late writes its own note after it (D-30). The executor's activities run on
their own `soc-executor` queue, in their own process (T-33 (1)). The case does not wait for them:
each call runs beside it, after the earlier calls of the same activity, so an offense's notes
reach QRadar in order; only closing the case or continuing as new waits for the calls in
progress. A failure that stays after the retries changes neither the decision record nor the
workflow. With writes switched off (shadow mode, T-23) the same calls are made and the executor
records them as `disabled`.

Any agent's run the model's outage ended (a model request that failed for good, or a run out of
its wall clock) is run once more after the configured wait (D-33); meanwhile only the SLA timer
marks the case `no_ai_decision`.

Between evaluations the case waits. `offense_updated` with a version not checked yet makes the
case fetch the offense and record it; the update is evaluated only when the reevaluation rules
say so (D-31). An update that only brings more events before the interval has passed plans one
evaluation for the end of the interval, which any evaluation before it drops (T-30 (1)). While
the case is `no_ai_decision` the interval is the retry wait, not the re-evaluation interval
(T-30 (2)). The intake does not report an update of an offense whose rules are all `skip` now,
so its current decision stays. `offense_closed` ends the workflow and abandons a chain in
progress. A long-lived case continues as new when Temporal suggests it.
"""

import asyncio
from datetime import datetime, timedelta
from enum import StrEnum

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import (
    ActivityError,
    CancelledError,
    ChildWorkflowError,
    WorkflowAlreadyStartedError,
)

from ais0c_workflows._activity import SOURCE_TIMEOUT, call
from ais0c_workflows.agent import AgentOutcome, AgentRequest
from ais0c_workflows.names import (
    AGENT_RETRY_DELAY,
    AGENT_WORKFLOW,
    CANDIDATE_SKILLS,
    CASE_STATE,
    CASE_URL,
    CASE_WORKFLOW,
    CLOSE_CASE,
    ENRICH_OFFENSE,
    EVALUATION_WINDOW,
    EXECUTOR_TASK_QUEUE,
    FETCH_OFFENSE,
    MARK_NO_AI_DECISION,
    OFFENSE_CLOSED,
    OFFENSE_UPDATED,
    PLAN_BUDGETS,
    RECORD_DECISION,
    RECORD_OFFENSE_UPDATE,
    RECORD_PLAN,
    REEVALUATION_INTERVAL,
    SEND_EMAIL,
    START_EVALUATION,
    TRIAGE_WORKFLOW,
    WRITE_OFFENSE_NOTE,
    agent_workflow_id,
    triage_workflow_id,
)
from ais0c_workflows.reevaluation import reevaluation_due
from ais0c_workflows.triage import MODEL_ACCESS_FAILURES, TriageOutcome, TriageRequest

with workflow.unsafe.imports_passed_through():
    from pydantic import AwareDatetime, BaseModel, ConfigDict

    from ais0c_contracts import (
        AgentResult,
        Budget,
        CasePlan,
        CaseReport,
        EnrichmentContext,
        InvestigationResult,
        Level,
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
    from ais0c_workflows.notify import (
        ALERT_LEVELS,
        EXECUTOR_ATTEMPT_TIMEOUT,
        EXECUTOR_RETRY,
        EXECUTOR_TOTAL_TIMEOUT,
        CaseAlertRequest,
        EvaluationNoteRequest,
        NoDecisionNoteRequest,
        case_alert,
        evaluation_note,
        no_decision_note,
        note_content,
    )
    from ais0c_workflows.plan import (
        INVESTIGATION,
        VERIFICATION,
        PlanCandidate,
        PlanDecision,
        validate_plan,
    )


class CaseStatus(StrEnum):
    """The values of `cases.status` (docs/impl/data-model.md)."""

    RUNNING = "running"
    DECIDED = "decided"
    NO_AI_DECISION = "no_ai_decision"
    CLOSED = "closed"


class CaseView(BaseModel):
    """The case as the workflow sees it: the `state` query and the workflow's result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    offense_id: int
    status: CaseStatus
    evaluation_no: int
    notify_level: Level | None


class CaseCarry(BaseModel):
    """State handed to the next run by Continue-As-New."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CaseStatus
    evaluation_no: int
    notify_level: Level | None
    evaluated_offense: OffenseSnapshot | None
    evaluated_at: AwareDatetime | None
    checked_version: AwareDatetime | None
    latest_version: AwareDatetime | None
    # When the deferred evaluation of an update is due (T-30 (1)); None when none is planned.
    deferred_at: AwareDatetime | None = None


@workflow.defn(name=CASE_WORKFLOW)
class CaseWorkflow:
    def __init__(self) -> None:
        self._case_id = ""
        self._offense_id = 0
        self._status = CaseStatus.RUNNING
        self._evaluation_no = 0
        self._notify_level: Level | None = None
        # The offense as the last evaluation saw it, and when that evaluation started: what an
        # update is compared with (D-31).
        self._evaluated_offense: OffenseSnapshot | None = None
        self._evaluated_at: datetime | None = None
        # `last_updated_time` of the newest offense version checked, evaluated or not, and of
        # the newest one announced by `offense_updated`.
        self._checked_version: datetime | None = None
        self._latest_version: datetime | None = None
        self._deferred_at: datetime | None = None
        self._closed = False
        # The executor's calls in progress: the last one of each activity (`_write`).
        self._writes: dict[str, asyncio.Task[None]] = {}
        self._case_url: str | None = None

    @workflow.run
    async def run(self, offense_id: int, carry: CaseCarry | None = None) -> CaseView:
        self._case_id = workflow.info().workflow_id
        self._offense_id = offense_id
        if carry is not None:
            self._restore(carry)
        while not self._closed:
            if self._evaluation_no == 0:
                await self._evaluate(await self._fetch())
            elif self._update_due() or self._deferred_due():
                await self._check_update()
            elif workflow.info().is_continue_as_new_suggested():
                if self._writing():
                    # The executor's calls end in this run; a signal meanwhile is seen next.
                    await self._writes_done()
                    continue
                workflow.continue_as_new(args=[offense_id, self._carry()])
            else:
                await self._wait()
        await call(CLOSE_CASE, self._case_id, offense_id, result_type=type(None))
        self._status = CaseStatus.CLOSED
        # The notes and e-mails of the case's last evaluations still go out.
        await self._writes_done()
        return self.state()

    @workflow.signal(name=OFFENSE_UPDATED)
    def offense_updated(self, last_updated_time: datetime) -> None:
        """The offense changed in QRadar. A version already checked changes nothing."""
        if self._latest_version is None or last_updated_time > self._latest_version:
            self._latest_version = last_updated_time

    @workflow.signal(name=OFFENSE_CLOSED)
    def offense_closed(self) -> None:
        """The offense was closed in QRadar; an evaluation in progress is abandoned."""
        self._closed = True

    @workflow.query(name=CASE_STATE)
    def state(self) -> CaseView:
        return CaseView(
            case_id=self._case_id,
            offense_id=self._offense_id,
            status=self._status,
            evaluation_no=self._evaluation_no,
            notify_level=self._notify_level,
        )

    def _update_due(self) -> bool:
        return self._latest_version is not None and (
            self._checked_version is None or self._latest_version > self._checked_version
        )

    def _deferred_due(self) -> bool:
        return self._deferred_at is not None and workflow.now() >= self._deferred_at

    async def _wait(self) -> None:
        """Wait for a signal, or for the deferred evaluation's time if one is planned."""
        timeout = None if self._deferred_at is None else self._deferred_at - workflow.now()
        try:
            await workflow.wait_condition(
                lambda: self._closed or self._update_due(),
                timeout=timeout,
                timeout_summary="deferred evaluation",
            )
        except TimeoutError:
            pass

    async def _fetch(self) -> OffenseSnapshot:
        announced = self._latest_version
        offense = await call(
            FETCH_OFFENSE,
            self._offense_id,
            result_type=OffenseSnapshot,
            attempt_timeout=SOURCE_TIMEOUT,
        )
        # Updates announced before the fetch are covered by it, even if the source lags behind.
        self._checked_version = (
            offense.last_updated_time
            if announced is None
            else max(offense.last_updated_time, announced)
        )
        return offense

    async def _check_update(self) -> None:
        """Record the updated offense; evaluate it again when it is due (D-31, T-30)."""
        if self._deferred_due():
            # The planned evaluation is this check; a later one may plan another.
            self._deferred_at = None
        offense = await self._fetch()
        await call(RECORD_OFFENSE_UPDATE, offense, result_type=type(None))
        previous, evaluated_at = self._evaluated_offense, self._evaluated_at
        if previous is not None and evaluated_at is not None:
            due = reevaluation_due(
                previous,
                offense,
                last_evaluated_at=evaluated_at,
                min_interval=await self._reevaluation_interval(),
            )
            if due is None or due > workflow.now():
                if due is not None and self._deferred_at is None:
                    # Only more events, before the interval ended: one evaluation then.
                    self._deferred_at = due
                workflow.logger.info(
                    "offense %d updated at %s; not evaluated again now",
                    self._offense_id,
                    offense.last_updated_time.isoformat(),
                )
                return
        if not self._closed:
            await self._evaluate(offense)

    async def _reevaluation_interval(self) -> timedelta:
        """The wait before an update with only more events is evaluated: the re-evaluation
        interval, or the retry wait while the case has no AI decision (T-30 (2))."""
        name = (
            AGENT_RETRY_DELAY
            if self._status is CaseStatus.NO_AI_DECISION
            else REEVALUATION_INTERVAL
        )
        return await call(name, result_type=timedelta)

    async def _evaluate(self, offense: OffenseSnapshot) -> None:
        self._evaluation_no += 1
        evaluation_no = self._evaluation_no
        self._evaluated_offense, self._evaluated_at = offense, workflow.now()
        # An evaluation drops the one planned for later (T-30 (1)).
        self._deferred_at = None
        enrichment = await call(ENRICH_OFFENSE, offense, result_type=EnrichmentContext)
        info = workflow.info()
        sla_due_at = await call(
            START_EVALUATION,
            self._case_id,
            evaluation_no,
            offense,
            enrichment.floor_level,
            info.workflow_id,
            info.run_id,
            result_type=datetime,
        )
        self._status = CaseStatus.RUNNING

        chain = asyncio.create_task(self._chain(evaluation_no, offense, enrichment))
        if not await self._settled_by(sla_due_at, chain):
            await self._no_ai_decision(evaluation_no)
            await workflow.wait_condition(lambda: chain.done() or self._closed)
        if not chain.done():
            # Closed: the agents' runs are abandoned and finish on their own (see _child).
            chain.cancel()
            return
        decision = chain.result()
        if decision is None:
            if self._status is CaseStatus.RUNNING:
                await self._no_ai_decision(evaluation_no)
            return
        decided_at = workflow.now()
        self._notify_level = await call(
            RECORD_DECISION,
            self._case_id,
            evaluation_no,
            decision.verdict,
            decision.confidence,
            decision.ai_level,
            decision.notify_level,
            enrichment.floor_level,
            decision.report,
            list(decision.qa_reasons),
            offense.rule_ids,
            decided_at,
            result_type=Level,
        )
        self._status = CaseStatus.DECIDED
        await self._notify(evaluation_no, offense, decision, self._notify_level, decided_at)

    # --- the chain ---------------------------------------------------------------------------

    async def _chain(
        self, evaluation_no: int, offense: OffenseSnapshot, enrichment: EnrichmentContext
    ) -> ChainDecision | None:
        """The evaluation's agent chain; None when Triage gives no decision."""
        triage = await self._triage(evaluation_no, offense, enrichment)
        if triage is None:
            return None
        window = await call(EVALUATION_WINDOW, offense, result_type=TimeWindow)
        candidates = await self._candidates(offense, enrichment)
        plan, planned = await self._plan(
            evaluation_no, offense, triage, window=window, candidates=candidates
        )
        investigation: InvestigationResult | None = None
        verification: VerificationResult | None = None
        for step in plan.steps:
            decision = decision_of(triage, investigation)
            if step.agent_id == INVESTIGATION:
                inputs = InvestigationInput(
                    offense=offense,
                    enrichment=enrichment,
                    verdict=triage.verdict,
                    confidence=triage.confidence,
                    ai_level=triage.ai_level,
                    investigation_focus=tuple(triage.investigation_focus),
                    claims=tuple(triage.claims),
                    data_gaps=tuple(triage.data_gaps),
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
                objective=(
                    f"Write the report of QRadar offense {offense.offense_id} "
                    f"(evaluation {evaluation_no})."
                ),
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
            objective=(
                f"Plan the rest of the evaluation of QRadar offense {offense.offense_id} "
                f"(evaluation {evaluation_no})."
            ),
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
        self, evaluation_no: int, offense: OffenseSnapshot, enrichment: EnrichmentContext
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

    # --- the rest ----------------------------------------------------------------------------

    async def _settled_by(self, deadline: datetime, chain: asyncio.Task[object]) -> bool:
        """Wait until the chain finishes or the case closes; False if the deadline comes first."""
        remaining = deadline - workflow.now()
        if remaining <= timedelta(0):
            return chain.done() or self._closed
        try:
            await workflow.wait_condition(
                lambda: chain.done() or self._closed, timeout=remaining, timeout_summary="sla"
            )
        except TimeoutError:
            return False
        return True

    async def _no_ai_decision(self, evaluation_no: int) -> None:
        """Mark the evaluation without an AI decision and write the "AI değerlendirmesi
        yapılamadı" note (criterion 4): at the SLA deadline, or when the chain ends without a
        decision. A decision that arrives late writes its own note after it (D-30)."""
        await call(MARK_NO_AI_DECISION, self._case_id, evaluation_no, result_type=type(None))
        self._status = CaseStatus.NO_AI_DECISION
        ended_at = workflow.now()
        self._write(
            WRITE_OFFENSE_NOTE,
            no_decision_note(
                case_id=self._case_id,
                offense_id=self._offense_id,
                evaluation_no=evaluation_no,
                case_url=await self._case_link(),
                evaluated_at=ended_at,
            ),
        )

    # --- the executor's note and e-mail --------------------------------------------------------

    async def _notify(
        self,
        evaluation_no: int,
        offense: OffenseSnapshot,
        decision: ChainDecision,
        level: Level,
        decided_at: datetime,
    ) -> None:
        """The note of the decision just recorded, and its alert e-mail when its notification
        level is high or critical (criteria 4 and 5).

        Both are built from the same evaluation's record: the decision, the level
        `record_decision` returned and the report, or the fixed summary without one. Whether a
        re-evaluation is e-mailed again is the executor's rule (D-42).
        """
        content = note_content(
            case_id=self._case_id,
            offense_id=offense.offense_id,
            evaluation_no=evaluation_no,
            case_url=await self._case_link(),
            verdict=decision.verdict,
            confidence=decision.confidence,
            notify_level=level,
            report=decision.report,
        )
        self._write(
            WRITE_OFFENSE_NOTE,
            evaluation_note(case_id=self._case_id, evaluated_at=decided_at, content=content),
        )
        if level in ALERT_LEVELS:
            self._write(
                SEND_EMAIL,
                case_alert(
                    case_id=self._case_id,
                    offense_name=offense.description,
                    evaluated_at=decided_at,
                    content=content,
                ),
            )

    async def _case_link(self) -> str:
        """The case's page on the platform, from the worker's setting (criterion 6)."""
        if self._case_url is None:
            self._case_url = await call(CASE_URL, self._case_id, result_type=str)
        return self._case_url

    def _write(
        self, name: str, request: EvaluationNoteRequest | NoDecisionNoteRequest | CaseAlertRequest
    ) -> None:
        """Start one executor call beside the case, after the earlier calls of activity `name`:
        an offense's notes reach QRadar in the order the case made them, and a note that is
        retried holds up neither the case nor the e-mail."""
        self._writes[name] = asyncio.create_task(
            self._executor_call(name, request, after=self._writes.get(name))
        )

    async def _executor_call(
        self,
        name: str,
        request: EvaluationNoteRequest | NoDecisionNoteRequest | CaseAlertRequest,
        *,
        after: asyncio.Task[None] | None,
    ) -> None:
        """One call to an executor activity on the `soc-executor` queue.

        The outcome is the executor's own record (`notes_written`, `notifications`), so the
        workflow does not read it; with writes off it is `disabled` (T-23). A failure the
        retries did not get past is logged and changes nothing else (criterion 8).
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

    def _writing(self) -> bool:
        return any(not task.done() for task in self._writes.values())

    async def _writes_done(self) -> None:
        """Wait for the executor's calls in progress, each at most EXECUTOR_TOTAL_TIMEOUT."""
        for task in list(self._writes.values()):
            await task

    def _carry(self) -> CaseCarry:
        return CaseCarry(
            status=self._status,
            evaluation_no=self._evaluation_no,
            notify_level=self._notify_level,
            evaluated_offense=self._evaluated_offense,
            evaluated_at=self._evaluated_at,
            checked_version=self._checked_version,
            latest_version=self._latest_version,
            deferred_at=self._deferred_at,
        )

    def _restore(self, carry: CaseCarry) -> None:
        self._status = carry.status
        self._evaluation_no = carry.evaluation_no
        self._notify_level = carry.notify_level
        self._evaluated_offense = carry.evaluated_offense
        self._evaluated_at = carry.evaluated_at
        self._checked_version = carry.checked_version
        self._latest_version = carry.latest_version
        self._deferred_at = carry.deferred_at


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
