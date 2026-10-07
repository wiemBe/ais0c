"""GroupCaseWorkflow: the case of an offense group in storm, from the storm until the group's
window ends (architecture §9, "Offense gruplama ve fırtına koruması"; T-14, T-22, T-62; task
T-027).

OffenseIntake starts it with the ID `group-<group_id>` when the group takes its first offense
over the hourly full analysis limit (the storm), and wakes it with `group_updated` whenever the
group takes another (`wake_group_cases`). The case:

1. Waits the settle time after the storm began (`AIS0C_GROUP_SETTLE_MINUTES`, T-62); the
   offenses that arrive meanwhile join the group and its summary.
2. Evaluates the group as one case: the summary of its offenses (`group_case_state`), the
   snapshot of the oldest offense the group took, its enrichment with the group's catalog
   entries, and CaseWorkflow's agent chain with the summary in Triage's input. The SLA runs from
   the storm's start for the first evaluation and from the evaluation's start for a later one,
   at the level of the group's highest floor. When the deadline passes first the case becomes
   `no_ai_decision`; a decision that comes later replaces it (D-30).
3. After a decision, writes the group note on every offense the group took (`group_id` set,
   the short format of T-019) and, when the level is high or critical, the group's alert e-mail;
   the executor sends it only when the level rose above the group's alerts already sent (D-42).
   An offense the group takes later gets the latest decision's note when the case wakes. An
   offense gets a second note only when the group's verdict changed since its note, so the same
   decision never reaches it twice; the executor's run marker guards a retry (T-045).
   An evaluation that ends without a decision (at the SLA deadline, or with the chain giving no
   decision) writes the "AI değerlendirmesi yapılamadı" note on every offense the group had
   taken (T-65 (2)): one per offense, with that evaluation's run marker. An offense the group
   takes while the evaluation still stands without a decision gets the same note when the case
   wakes. A decision that arrives late writes the group note beside it (D-30).
4. Evaluates the group again (T-22):
   - 24 hours after the last evaluation;
   - when the group has twice the offenses it had at the last evaluation;
   - when the source or destination most of the group's offenses carry is another than at the
     last evaluation (`GroupValues.leader`);
   - after an evaluation without a decision, when the group takes another offense, once the
     retry wait has passed (as an offense case's update, T-30 (2)).
   The volume and leader rules wait at least the re-evaluation interval after the last
   evaluation (D-31), so a distribution that flaps cannot run the chain again and again.
5. Ends when the group's window has ended, 24 hours after its last offense: the group and its
   case are closed (`close_group_case`). The notes and e-mails in progress still go out.

A long-lived case continues as new when Temporal suggests it.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Final

from temporalio import workflow

from ais0c_workflows._activity import SOURCE_TIMEOUT, call
from ais0c_workflows.names import (
    AGENT_RETRY_DELAY,
    BEGIN_GROUP_EVALUATION,
    CASE_STATE,
    CLOSE_GROUP_CASE,
    ENRICH_GROUP,
    FETCH_OFFENSE,
    GROUP_CASE_STATE,
    GROUP_CASE_WORKFLOW,
    GROUP_SETTLE_DELAY,
    GROUP_UPDATED,
    MARK_NO_AI_DECISION,
    RECORD_DECISION,
    REEVALUATION_INTERVAL,
    SEND_EMAIL,
    WRITE_OFFENSE_NOTE,
)

with workflow.unsafe.imports_passed_through():
    from pydantic import AwareDatetime, BaseModel, ConfigDict

    from ais0c_contracts import (
        CaseVerdict,
        EnrichmentContext,
        Level,
        NoteContent,
        OffenseSnapshot,
    )
    from ais0c_workflows.case import CaseStatus
    from ais0c_workflows.chain import ChainDecision
    from ais0c_workflows.evaluation import AgentChain, ExecutorCalls
    from ais0c_workflows.group_summary import GroupSummary
    from ais0c_workflows.notify import (
        ALERT_LEVELS,
        group_alert,
        group_note,
        no_decision_note,
        note_content,
    )

# A group decision is evaluated again after this long (T-22).
GROUP_DECISION_LIFETIME: Final = timedelta(hours=24)
# The group's window has ended once it lies this far behind; `close_group_case` closes a window
# that ended before the time it is given.
WINDOW_MARGIN: Final = timedelta(seconds=1)

type GroupState = tuple[GroupSummary | None, list[int], datetime]


class GroupView(BaseModel):
    """The group case as the workflow sees it: the `state` query and the workflow's result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    group_id: str
    status: CaseStatus
    evaluation_no: int
    notify_level: Level | None
    # The offenses that carry the group's latest verdict in a note.
    noted: int


class GroupDecision(BaseModel):
    """The latest decision of the group, as its notes and e-mail carry it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: NoteContent
    decided_at: AwareDatetime


class NoDecisionNotes(BaseModel):
    """The latest evaluation that ended without an AI decision: when it ended, the case's link
    and the offenses its note has reached (T-65 (2))."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_no: int
    evaluated_at: AwareDatetime
    case_url: str
    noted: set[int]


class GroupCarry(BaseModel):
    """State handed to the next run by Continue-As-New."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    storm_started_at: AwareDatetime
    status: CaseStatus
    evaluation_no: int
    notify_level: Level | None
    evaluated_at: AwareDatetime | None
    evaluated_count: int
    evaluated_source: str | None
    evaluated_destination: str | None
    decision: GroupDecision | None
    # The verdict each offense's latest group note carries.
    noted: dict[int, CaseVerdict]
    no_decision: NoDecisionNotes | None = None
    window_end: AwareDatetime | None
    due_at: AwareDatetime | None
    updated: bool


@workflow.defn(name=GROUP_CASE_WORKFLOW)
class GroupCaseWorkflow:
    def __init__(self) -> None:
        self._group_id = ""
        self._case_id = ""
        self._storm_started_at: datetime | None = None
        self._status = CaseStatus.RUNNING
        self._evaluation_no = 0
        self._notify_level: Level | None = None
        # What the last evaluation saw: when it started, the group's size and leading values.
        self._evaluated_at: datetime | None = None
        self._evaluated_count = 0
        self._evaluated_source: str | None = None
        self._evaluated_destination: str | None = None
        self._decision: GroupDecision | None = None
        self._noted: dict[int, CaseVerdict] = {}
        self._no_decision: NoDecisionNotes | None = None
        self._window_end: datetime | None = None
        # When the next evaluation is due: the end of the settle time, then 24 hours after the
        # last evaluation or earlier when a re-evaluation rule plans it.
        self._due_at: datetime | None = None
        self._updated = False
        self._closed = False
        self._chain = AgentChain(self._case_id)
        self._executor = ExecutorCalls(self._case_id)

    @workflow.run
    async def run(self, group_id: str, carry: GroupCarry | None = None) -> GroupView:
        self._group_id = group_id
        self._case_id = workflow.info().workflow_id
        self._chain = AgentChain(self._case_id)
        self._executor = ExecutorCalls(self._case_id)
        if carry is None:
            self._storm_started_at = workflow.now()
            settle = await call(GROUP_SETTLE_DELAY, result_type=timedelta)
            self._due_at = self._storm_started_at + settle
        else:
            self._restore(carry)
        while not self._closed:
            now = workflow.now()
            if self._window_end is not None and now > self._window_end:
                await self._close_if_ended()
            elif self._due_at is not None and now >= self._due_at:
                self._due_at = None
                await self._evaluate()
            elif self._updated:
                self._updated = False
                await self._check()
            elif workflow.info().is_continue_as_new_suggested():
                if self._executor.writing():
                    # The executor's calls end in this run; a signal meanwhile is seen next.
                    await self._executor.done()
                    continue
                workflow.continue_as_new(args=[group_id, self._carry()])
            else:
                await self._wait()
        # The notes and e-mails of the group's last decision still go out.
        await self._executor.done()
        return self.state()

    @workflow.signal(name=GROUP_UPDATED)
    def group_updated(self) -> None:
        """The group took another offense; the case reads the group when it can."""
        self._updated = True

    @workflow.query(name=CASE_STATE)
    def state(self) -> GroupView:
        verdict = None if self._decision is None else self._decision.content.verdict
        return GroupView(
            case_id=self._case_id,
            group_id=self._group_id,
            status=self._status,
            evaluation_no=self._evaluation_no,
            notify_level=self._notify_level,
            noted=sum(1 for noted in self._noted.values() if noted is verdict),
        )

    async def _wait(self) -> None:
        """Wait for a signal, the next evaluation or the end of the group's window."""
        deadlines = [
            deadline
            for deadline in (
                self._due_at,
                None if self._window_end is None else self._window_end + WINDOW_MARGIN,
            )
            if deadline is not None
        ]
        timeout = None if not deadlines else min(deadlines) - workflow.now()
        try:
            await workflow.wait_condition(
                lambda: self._updated, timeout=timeout, timeout_summary="group case"
            )
        except TimeoutError:
            pass

    async def _state(self) -> GroupState:
        summary, grouped, window_end = await call(
            GROUP_CASE_STATE,
            self._group_id,
            result_type=tuple[GroupSummary | None, list[int], datetime],
        )
        self._window_end = window_end
        return summary, grouped, window_end

    async def _check(self) -> None:
        """The group took offenses: note them with the latest decision, and plan the next
        evaluation when a re-evaluation rule says so."""
        summary, grouped, _ = await self._state()
        self._note(grouped)
        self._note_no_decision(grouped)
        if summary is None:
            return
        now = workflow.now()
        if self._evaluation_no == 0:
            if self._due_at is None:
                # The settle time is over, but the group had nothing to evaluate then.
                self._plan(now)
            return
        evaluated_at = self._evaluated_at or now
        if self._status is CaseStatus.NO_AI_DECISION:
            retry = await call(AGENT_RETRY_DELAY, result_type=timedelta)
            self._plan(max(now, evaluated_at + retry))
            return
        source, destination = summary.source_ips.leader(), summary.destination_ips.leader()
        if (
            summary.offense_count >= 2 * self._evaluated_count
            or source not in (None, self._evaluated_source)
            or destination not in (None, self._evaluated_destination)
        ):
            interval = await call(REEVALUATION_INTERVAL, result_type=timedelta)
            self._plan(max(now, evaluated_at + interval))

    def _plan(self, at: datetime) -> None:
        """Bring the next evaluation forward to `at`."""
        self._due_at = at if self._due_at is None else min(self._due_at, at)

    async def _evaluate(self) -> None:
        summary, grouped, _ = await self._state()
        self._note(grouped)
        if summary is None:
            workflow.logger.warning("group %s has no offense to evaluate", self._group_id)
            return
        self._evaluation_no += 1
        evaluation_no = self._evaluation_no
        started_at = workflow.now()
        self._evaluated_at = started_at
        self._evaluated_count = summary.offense_count
        self._evaluated_source = summary.source_ips.leader()
        self._evaluated_destination = summary.destination_ips.leader()
        self._due_at = started_at + GROUP_DECISION_LIFETIME
        offense = await call(
            FETCH_OFFENSE,
            summary.example_offense_id,
            result_type=OffenseSnapshot,
            attempt_timeout=SOURCE_TIMEOUT,
        )
        enrichment = await call(
            ENRICH_GROUP, self._group_id, offense, summary, result_type=EnrichmentContext
        )
        info = workflow.info()
        sla_start = self._storm_started_at if evaluation_no == 1 else started_at
        sla_due_at = await call(
            BEGIN_GROUP_EVALUATION,
            self._case_id,
            self._group_id,
            evaluation_no,
            enrichment.floor_level,
            sla_start,
            info.workflow_id,
            info.run_id,
            result_type=datetime,
        )
        self._status = CaseStatus.RUNNING

        chain = asyncio.create_task(
            self._chain.run(evaluation_no, offense, enrichment, group_summary=summary)
        )
        if not await _settled_by(sla_due_at, chain):
            await self._no_ai_decision(evaluation_no, grouped)
            await workflow.wait_condition(chain.done)
        decision = chain.result()
        if decision is None:
            if self._status is CaseStatus.RUNNING:
                await self._no_ai_decision(evaluation_no, grouped)
            return
        await self._decided(evaluation_no, summary, offense, enrichment, decision)

    async def _decided(
        self,
        evaluation_no: int,
        summary: GroupSummary,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        decision: ChainDecision,
    ) -> None:
        """Record the decision, send the group's alert when it is high or critical and note
        the offenses the group took (criteria 2 and 3)."""
        decided_at = workflow.now()
        level = await call(
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
            [rule.rule_id for rule in summary.rules],
            decided_at,
            result_type=Level,
        )
        self._notify_level = level
        self._status = CaseStatus.DECIDED
        # The no-decision phase of this evaluation is over: an offense the group takes from now
        # on gets the decision's note, not the no-decision note (T-65 (2)).
        self._no_decision = None
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
        self._decision = GroupDecision(content=content, decided_at=decided_at)
        if level in ALERT_LEVELS:
            self._executor.write(
                SEND_EMAIL,
                group_alert(
                    case_id=self._case_id,
                    group_id=self._group_id,
                    title=offense.description,
                    offense_count=summary.offense_count,
                    evaluated_at=decided_at,
                    decision=content,
                ),
            )
        # The group is read afresh, so the offenses it took during the evaluation are noted.
        self._updated = True

    def _note(self, grouped: list[int]) -> None:
        """Write the latest decision's note on every offense the group took that does not carry
        its verdict yet."""
        decision = self._decision
        if decision is None:
            return
        verdict = decision.content.verdict
        for offense_id in grouped:
            if self._noted.get(offense_id) is verdict:
                continue
            self._noted[offense_id] = verdict
            self._executor.write(
                WRITE_OFFENSE_NOTE,
                group_note(
                    case_id=self._case_id,
                    group_id=self._group_id,
                    offense_id=offense_id,
                    evaluated_at=decision.decided_at,
                    decision=decision.content,
                ),
            )

    async def _no_ai_decision(self, evaluation_no: int, grouped: list[int]) -> None:
        """Mark the evaluation without an AI decision and write its note on every offense the
        group had taken (T-65 (2)); a later decision replaces it (D-30) and writes the group
        note beside this one."""
        await call(MARK_NO_AI_DECISION, self._case_id, evaluation_no, result_type=type(None))
        self._status = CaseStatus.NO_AI_DECISION
        ended_at = workflow.now()
        self._no_decision = NoDecisionNotes(
            evaluation_no=evaluation_no,
            evaluated_at=ended_at,
            case_url=await self._executor.case_link(),
            noted=set(),
        )
        self._note_no_decision(grouped)

    def _note_no_decision(self, grouped: list[int]) -> None:
        """The no-decision note of the latest evaluation without a decision, once per offense;
        offenses the group takes while it stands get it when the case wakes (T-65 (2))."""
        pending = self._no_decision
        if (
            pending is None
            or self._status is not CaseStatus.NO_AI_DECISION
            or pending.evaluation_no != self._evaluation_no
        ):
            return
        for offense_id in grouped:
            if offense_id in pending.noted:
                continue
            pending.noted.add(offense_id)
            self._executor.write(
                WRITE_OFFENSE_NOTE,
                no_decision_note(
                    case_id=self._case_id,
                    offense_id=offense_id,
                    evaluation_no=pending.evaluation_no,
                    case_url=pending.case_url,
                    evaluated_at=pending.evaluated_at,
                ),
            )

    async def _close_if_ended(self) -> None:
        """Close the group and its case if its window has ended; an offense may have moved it."""
        window_end = await call(
            CLOSE_GROUP_CASE,
            self._group_id,
            self._case_id,
            workflow.now(),
            result_type=datetime | None,  # pyright: ignore[reportArgumentType] - a union
        )
        if window_end is None:
            self._closed = True
            self._status = CaseStatus.CLOSED
        else:
            self._window_end = window_end

    def _carry(self) -> GroupCarry:
        if self._storm_started_at is None:  # pragma: no cover - set before the loop
            raise RuntimeError("the storm's start is unknown")
        return GroupCarry(
            storm_started_at=self._storm_started_at,
            status=self._status,
            evaluation_no=self._evaluation_no,
            notify_level=self._notify_level,
            evaluated_at=self._evaluated_at,
            evaluated_count=self._evaluated_count,
            evaluated_source=self._evaluated_source,
            evaluated_destination=self._evaluated_destination,
            decision=self._decision,
            noted=dict(self._noted),
            no_decision=self._no_decision,
            window_end=self._window_end,
            due_at=self._due_at,
            updated=self._updated,
        )

    def _restore(self, carry: GroupCarry) -> None:
        self._storm_started_at = carry.storm_started_at
        self._status = carry.status
        self._evaluation_no = carry.evaluation_no
        self._notify_level = carry.notify_level
        self._evaluated_at = carry.evaluated_at
        self._evaluated_count = carry.evaluated_count
        self._evaluated_source = carry.evaluated_source
        self._evaluated_destination = carry.evaluated_destination
        self._decision = carry.decision
        self._noted = dict(carry.noted)
        self._no_decision = (
            None
            if carry.no_decision is None
            else carry.no_decision.model_copy(update={"noted": set(carry.no_decision.noted)})
        )
        self._window_end = carry.window_end
        self._due_at = carry.due_at
        self._updated = self._updated or carry.updated


async def _settled_by(deadline: datetime, chain: asyncio.Task[object]) -> bool:
    """Wait until the chain finishes; False if the deadline comes first."""
    remaining = deadline - workflow.now()
    if remaining <= timedelta(0):
        return chain.done()
    try:
        await workflow.wait_condition(chain.done, timeout=remaining, timeout_summary="sla")
    except TimeoutError:
        return False
    return True
