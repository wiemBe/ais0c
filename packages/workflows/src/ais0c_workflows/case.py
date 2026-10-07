"""CaseWorkflow: the case of one offense, from its first evaluation until QRadar closes it
(architecture §6, §9, §20).

OffenseIntake starts it with the ID `case-<offense_id>`. An evaluation fetches the offense,
enriches it, opens the case (or starts the next evaluation), runs the agent chain and records the
decision. The SLA timer covers the whole chain: when the deadline passes first the case becomes
`no_ai_decision`, so the operator knows the AI has not looked at it. The chain keeps running and
a later decision still replaces that status (D-30).

The chain of one evaluation (T-026) is `ais0c_workflows.evaluation.AgentChain`: Triage, the
Orchestrator's plan, its steps and Reporting, each agent a child workflow. Without Triage's
decision the chain stops and the case is `no_ai_decision` until an update is evaluated.

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

An agent's run the model's outage ended is run once more after the configured wait (D-33);
meanwhile only the SLA timer marks the case `no_ai_decision`.

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

from ais0c_workflows._activity import SOURCE_TIMEOUT, call
from ais0c_workflows.names import (
    AGENT_RETRY_DELAY,
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
    SEND_EMAIL,
    START_EVALUATION,
    WRITE_OFFENSE_NOTE,
)
from ais0c_workflows.reevaluation import reevaluation_due

with workflow.unsafe.imports_passed_through():
    from pydantic import AwareDatetime, BaseModel, ConfigDict

    from ais0c_contracts import EnrichmentContext, Level, OffenseSnapshot
    from ais0c_workflows.chain import ChainDecision
    from ais0c_workflows.evaluation import AgentChain, ExecutorCalls
    from ais0c_workflows.notify import (
        ALERT_LEVELS,
        case_alert,
        evaluation_note,
        no_decision_note,
        note_content,
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
        self._chain = AgentChain(self._case_id)
        self._executor = ExecutorCalls(self._case_id)

    @workflow.run
    async def run(self, offense_id: int, carry: CaseCarry | None = None) -> CaseView:
        self._case_id = workflow.info().workflow_id
        self._offense_id = offense_id
        self._chain = AgentChain(self._case_id)
        self._executor = ExecutorCalls(self._case_id)
        if carry is not None:
            self._restore(carry)
        while not self._closed:
            if self._evaluation_no == 0:
                await self._evaluate(await self._fetch())
            elif self._update_due() or self._deferred_due():
                await self._check_update()
            elif workflow.info().is_continue_as_new_suggested():
                if self._executor.writing():
                    # The executor's calls end in this run; a signal meanwhile is seen next.
                    await self._executor.done()
                    continue
                workflow.continue_as_new(args=[offense_id, self._carry()])
            else:
                await self._wait()
        await call(CLOSE_CASE, self._case_id, offense_id, result_type=type(None))
        self._status = CaseStatus.CLOSED
        # The notes and e-mails of the case's last evaluations still go out.
        await self._executor.done()
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

        chain = asyncio.create_task(self._chain.run(evaluation_no, offense, enrichment))
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
        self._executor.write(
            WRITE_OFFENSE_NOTE,
            no_decision_note(
                case_id=self._case_id,
                offense_id=self._offense_id,
                evaluation_no=evaluation_no,
                case_url=await self._executor.case_link(),
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
            case_url=await self._executor.case_link(),
            verdict=decision.verdict,
            confidence=decision.confidence,
            notify_level=level,
            report=decision.report,
        )
        self._executor.write(
            WRITE_OFFENSE_NOTE,
            evaluation_note(case_id=self._case_id, evaluated_at=decided_at, content=content),
        )
        if level in ALERT_LEVELS:
            self._executor.write(
                SEND_EMAIL,
                case_alert(
                    case_id=self._case_id,
                    offense_name=offense.description,
                    evaluated_at=decided_at,
                    content=content,
                ),
            )

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
