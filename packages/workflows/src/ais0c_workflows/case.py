"""CaseWorkflow: the case of one offense, from its first evaluation until QRadar closes it
(architecture §6, §9, §20).

OffenseIntake starts it with the ID `case-<offense_id>`. An evaluation fetches the offense,
enriches it, opens the case (or starts the next evaluation), runs triage and records the
decision. The SLA timer runs alongside triage: when the deadline passes first the case becomes
`no_ai_decision`, so the operator knows the AI has not looked at it. Triage keeps running and a
later decision still replaces that status.

Between evaluations the case waits. `offense_updated` with a version not evaluated yet starts the
next evaluation; `offense_closed` ends the workflow. A long-lived case continues as new when
Temporal suggests it.
"""

import asyncio
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

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
    START_EVALUATION,
    TRIAGE,
)

with workflow.unsafe.imports_passed_through():
    from pydantic import AwareDatetime, BaseModel, ConfigDict

    from ais0c_contracts import EnrichmentContext, Level, OffenseSnapshot, TriageResult

# One triage attempt; the Triage manifest's wall clock budget is 180 seconds.
TRIAGE_TIMEOUT: Final = timedelta(minutes=5)
TRIAGE_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
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
    evaluated_version: AwareDatetime | None
    latest_version: AwareDatetime | None


@workflow.defn(name=CASE_WORKFLOW)
class CaseWorkflow:
    def __init__(self) -> None:
        self._case_id = ""
        self._offense_id = 0
        self._status = CaseStatus.RUNNING
        self._evaluation_no = 0
        self._notify_level: Level | None = None
        # `last_updated_time` of the newest offense version evaluated and of the newest one
        # announced by `offense_updated`.
        self._evaluated_version: datetime | None = None
        self._latest_version: datetime | None = None
        self._closed = False

    @workflow.run
    async def run(self, offense_id: int, carry: CaseCarry | None = None) -> CaseView:
        self._case_id = workflow.info().workflow_id
        self._offense_id = offense_id
        if carry is not None:
            self._restore(carry)
        while not self._closed:
            if self._evaluation_due():
                await self._evaluate()
            elif workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(args=[offense_id, self._carry()])
            else:
                await workflow.wait_condition(lambda: self._closed or self._evaluation_due())
        await call(CLOSE_CASE, self._case_id, offense_id, result_type=type(None))
        self._status = CaseStatus.CLOSED
        return self.state()

    @workflow.signal(name=OFFENSE_UPDATED)
    def offense_updated(self, last_updated_time: datetime) -> None:
        """The offense changed in QRadar. A version already evaluated changes nothing."""
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

    def _evaluation_due(self) -> bool:
        if self._evaluation_no == 0:
            return True
        return self._latest_version is not None and (
            self._evaluated_version is None or self._latest_version > self._evaluated_version
        )

    async def _evaluate(self) -> None:
        self._evaluation_no += 1
        evaluation_no = self._evaluation_no
        announced = self._latest_version
        offense = await call(
            FETCH_OFFENSE,
            self._offense_id,
            result_type=OffenseSnapshot,
            attempt_timeout=SOURCE_TIMEOUT,
        )
        # Updates announced before the fetch are covered by it, even if the source lags behind.
        self._evaluated_version = (
            offense.last_updated_time
            if announced is None
            else max(offense.last_updated_time, announced)
        )
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
        try:
            result = triage.result()
        except ActivityError:
            workflow.logger.warning("triage failed: %s evaluation %d", self._case_id, evaluation_no)
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
    ) -> TriageResult:
        """The Triage step. T-012 replaces this activity call with the Triage agent run through
        Pydantic AI's TemporalDurability; the rest of the workflow stays as it is.

        Closing the case abandons triage instead of asking the server to cancel it: a cancel
        request that crosses the activity's completion is rejected and fails the workflow task.
        """
        return await call(
            TRIAGE,
            self._case_id,
            evaluation_no,
            offense,
            enrichment,
            result_type=TriageResult,
            attempt_timeout=TRIAGE_TIMEOUT,
            retry_policy=TRIAGE_RETRY,
            cancellation_type=workflow.ActivityCancellationType.ABANDON,
        )

    async def _settled_by(self, deadline: datetime, triage: asyncio.Task[TriageResult]) -> bool:
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
            evaluated_version=self._evaluated_version,
            latest_version=self._latest_version,
        )

    def _restore(self, carry: CaseCarry) -> None:
        self._status = carry.status
        self._evaluation_no = carry.evaluation_no
        self._notify_level = carry.notify_level
        self._evaluated_version = carry.evaluated_version
        self._latest_version = carry.latest_version
