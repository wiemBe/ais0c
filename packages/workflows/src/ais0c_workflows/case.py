"""CaseWorkflow: the case of one offense, from its first evaluation until QRadar closes it
(architecture §6, §9, §20).

OffenseIntake starts it with the ID `case-<offense_id>`. An evaluation fetches the offense,
enriches it, opens the case (or starts the next evaluation), runs triage and records the
decision. The SLA timer runs alongside triage: when the deadline passes first the case becomes
`no_ai_decision`, so the operator knows the AI has not looked at it. Triage keeps running and a
later decision still replaces that status.

Triage is the child workflow TriageWorkflow, one per evaluation: the Triage agent runs there
through Pydantic AI's TemporalDurability. A run the model's outage ended (a model request that
failed for good, or a run out of its wall clock) is run once more after the configured wait
(D-33); meanwhile only the SLA timer marks the case `no_ai_decision`. A run that ends without a
decision for another reason (invalid output, the token, tool call or step budget, a failed tool
call), or a second run that fails too, leaves the case `no_ai_decision` until an update is
evaluated.

Between evaluations the case waits. `offense_updated` with a version not checked yet makes the
case fetch the offense and record it; the update is evaluated only when `should_reevaluate` says
so (D-31). The intake does not report an update of an offense whose rules are all `skip` now, so
its current decision stays. `offense_closed` ends the workflow. A long-lived case continues as
new when Temporal suggests it.
"""

import asyncio
from datetime import datetime, timedelta
from enum import StrEnum

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ChildWorkflowError, WorkflowAlreadyStartedError

from ais0c_workflows._activity import SOURCE_TIMEOUT, call
from ais0c_workflows.names import (
    CASE_STATE,
    CASE_WORKFLOW,
    CLOSE_CASE,
    ENRICH_OFFENSE,
    FETCH_OFFENSE,
    MARK_NO_AI_DECISION,
    OFFENSE_CLOSED,
    OFFENSE_UPDATED,
    RECORD_DECISION,
    RECORD_OFFENSE_UPDATE,
    REEVALUATION_INTERVAL,
    START_EVALUATION,
    TRIAGE_RETRY_DELAY,
    TRIAGE_WORKFLOW,
    triage_workflow_id,
)
from ais0c_workflows.reevaluation import should_reevaluate
from ais0c_workflows.triage import MODEL_ACCESS_FAILURES, TriageOutcome, TriageRequest

with workflow.unsafe.imports_passed_through():
    from pydantic import AwareDatetime, BaseModel, ConfigDict

    from ais0c_contracts import EnrichmentContext, Level, OffenseSnapshot, RunStatus, TriageResult


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
        self._closed = False

    @workflow.run
    async def run(self, offense_id: int, carry: CaseCarry | None = None) -> CaseView:
        self._case_id = workflow.info().workflow_id
        self._offense_id = offense_id
        if carry is not None:
            self._restore(carry)
        while not self._closed:
            if self._evaluation_no == 0:
                await self._evaluate(await self._fetch())
            elif self._update_due():
                await self._check_update()
            elif workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(args=[offense_id, self._carry()])
            else:
                await workflow.wait_condition(lambda: self._closed or self._update_due())
        await call(CLOSE_CASE, self._case_id, offense_id, result_type=type(None))
        self._status = CaseStatus.CLOSED
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
        """Record the updated offense; evaluate it again only when D-31 says so."""
        offense = await self._fetch()
        await call(RECORD_OFFENSE_UPDATE, offense, result_type=type(None))
        previous, evaluated_at = self._evaluated_offense, self._evaluated_at
        if previous is not None and evaluated_at is not None:
            interval = await call(REEVALUATION_INTERVAL, result_type=timedelta)
            if not should_reevaluate(
                previous,
                offense,
                last_evaluated_at=evaluated_at,
                now=workflow.now(),
                min_interval=interval,
            ):
                workflow.logger.info(
                    "offense %d updated at %s; not evaluated again",
                    self._offense_id,
                    offense.last_updated_time.isoformat(),
                )
                return
        if not self._closed:
            await self._evaluate(offense)

    async def _evaluate(self, offense: OffenseSnapshot) -> None:
        self._evaluation_no += 1
        evaluation_no = self._evaluation_no
        self._evaluated_offense, self._evaluated_at = offense, workflow.now()
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

        triage = asyncio.create_task(self._triage(evaluation_no, offense, enrichment))
        if not await self._settled_by(sla_due_at, triage):
            await self._no_ai_decision(evaluation_no)
            await workflow.wait_condition(lambda: triage.done() or self._closed)
        if not triage.done():
            triage.cancel()
            return
        result = triage.result()
        if result is None:
            if self._status is CaseStatus.RUNNING:
                await self._no_ai_decision(evaluation_no)
            return
        self._notify_level = await call(
            RECORD_DECISION,
            self._case_id,
            evaluation_no,
            result,
            enrichment.floor_level,
            workflow.now(),
            result_type=Level,
        )
        self._status = CaseStatus.DECIDED

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
            delay = await call(TRIAGE_RETRY_DELAY, result_type=timedelta)
            workflow.logger.warning(
                "triage run %s is retried in %s: %s", outcome.run_id, delay, outcome.failure
            )
            await workflow.sleep(delay, summary="triage retry")
            outcome = await self._triage_run(request, retry=True)
        if outcome is None or outcome.status is not RunStatus.COMPLETED:
            return None
        return outcome.result

    async def _triage_run(self, request: TriageRequest, *, retry: bool) -> TriageOutcome | None:
        """One Triage run; None when its workflow failed.

        The run is the child workflow TriageWorkflow, so its many activities stay out of this
        history. Closing the case abandons the child instead of cancelling it: the run finishes
        on its own and records itself, and no cancel request can cross its completion. Its ID
        is never reused, so an evaluation is triaged at most once, and retried at most once.
        """
        run_id = triage_workflow_id(self._case_id, request.evaluation_no, retry=retry)
        try:
            outcome = await workflow.execute_child_workflow(
                TRIAGE_WORKFLOW,
                request,
                id=run_id,
                result_type=TriageOutcome,
                cancellation_type=workflow.ChildWorkflowCancellationType.ABANDON,
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except (ChildWorkflowError, WorkflowAlreadyStartedError) as error:
            workflow.logger.warning("triage run %s failed: %s", run_id, error)
            return None
        if outcome.status is not RunStatus.COMPLETED or outcome.result is None:
            workflow.logger.warning(
                "triage run %s ended %s (%s): %s",
                run_id,
                outcome.status,
                outcome.failure,
                outcome.error,
            )
        return outcome

    async def _settled_by(
        self, deadline: datetime, triage: asyncio.Task[TriageResult | None]
    ) -> bool:
        """Wait until triage finishes or the case closes; False if the deadline comes first."""
        remaining = deadline - workflow.now()
        if remaining <= timedelta(0):
            return triage.done() or self._closed
        try:
            await workflow.wait_condition(
                lambda: triage.done() or self._closed, timeout=remaining, timeout_summary="sla"
            )
        except TimeoutError:
            return False
        return True

    async def _no_ai_decision(self, evaluation_no: int) -> None:
        await call(MARK_NO_AI_DECISION, self._case_id, evaluation_no, result_type=type(None))
        self._status = CaseStatus.NO_AI_DECISION

    def _carry(self) -> CaseCarry:
        return CaseCarry(
            status=self._status,
            evaluation_no=self._evaluation_no,
            notify_level=self._notify_level,
            evaluated_offense=self._evaluated_offense,
            evaluated_at=self._evaluated_at,
            checked_version=self._checked_version,
            latest_version=self._latest_version,
        )

    def _restore(self, carry: CaseCarry) -> None:
        self._status = carry.status
        self._evaluation_no = carry.evaluation_no
        self._notify_level = carry.notify_level
        self._evaluated_offense = carry.evaluated_offense
        self._evaluated_at = carry.evaluated_at
        self._checked_version = carry.checked_version
        self._latest_version = carry.latest_version
