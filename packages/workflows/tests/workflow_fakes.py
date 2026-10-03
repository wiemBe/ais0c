"""Fake activities, stand-in workflows and synthetic payloads for the workflow tests.

The fakes are registered under the real activity and workflow names, so the workflows run
unchanged. IPs are from the RFC 5737 ranges.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseVerdict,
    CatalogContext,
    Confidence,
    EnrichmentContext,
    Level,
    OffenseSnapshot,
    RunStatus,
    TimeWindow,
    TriageResult,
    Usage,
)
from ais0c_workflows import TriageFailure, TriageOutcome, TriageRequest
from ais0c_workflows.names import (
    ADMIT_OFFENSES,
    BEGIN_TRIAGE_RUN,
    CASE_STATE,
    CLOSE_CASE,
    ENRICH_OFFENSE,
    FETCH_OFFENSE,
    FETCH_OFFENSE_CHANGES,
    FIND_CLOSED_OFFENSES,
    FINISH_TRIAGE_RUN,
    MARK_NO_AI_DECISION,
    NEXT_PENDING_OFFENSES,
    OFFENSE_CLOSED,
    OFFENSE_UPDATED,
    RECORD_DECISION,
    RECORD_OFFENSE_UPDATE,
    REEVALUATION_INTERVAL,
    START_CASE,
    START_EVALUATION,
    TRIAGE_RETRY_DELAY,
    TRIAGE_WORKFLOW,
)

WAIT_SECONDS = 10
# The activity of TriageStub that plays the test's script.
SCRIPTED_TRIAGE = "scripted_triage"
# The stub retries a failing script like the agent's activities retry a failing model request.
STUB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)
NO_USAGE = Usage(tokens=0, tool_calls=0, seconds=0.0)


def offense(
    offense_id: int,
    *,
    start: datetime,
    updated: datetime | None = None,
    rule_ids: Sequence[int] = (100201,),
    event_count: int = 12,
    log_source_ids: Sequence[int] = (112,),
    source_ips: Sequence[str] = ("203.0.113.7",),
    destination_ips: Sequence[str] = ("198.51.100.15",),
    usernames: Sequence[str] = (),
) -> OffenseSnapshot:
    return OffenseSnapshot(
        offense_id=offense_id,
        description="Excessive Firewall Accepts From Single Source",
        offense_type="Source IP",
        offense_source="203.0.113.7",
        rule_ids=list(rule_ids),
        rule_names=["FW: excessive accepts"],
        categories=["Firewall Permit"],
        magnitude=4,
        start_time=start,
        last_updated_time=start if updated is None else updated,
        event_count=event_count,
        log_source_ids=list(log_source_ids),
        source_ips=list(source_ips),
        destination_ips=list(destination_ips),
        usernames=list(usernames),
    )


def triage_result(ai_level: Level = Level.HIGH) -> TriageResult:
    return TriageResult(
        task_id="triage-task-1",
        status=RunStatus.COMPLETED,
        claims=[],
        data_gaps=[],
        injection_suspected=False,
        usage=Usage(tokens=0, tool_calls=0, seconds=0.0),
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=ai_level,
        rationale="Synthetic triage result.",
        needs_investigation=False,
        investigation_focus=[],
    )


class Events:
    """What the fake activities did, in order, as (name, number) pairs."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, int]] = []
        self._changed = asyncio.Condition()

    async def add(self, name: str, number: int = 0) -> None:
        async with self._changed:
            self.seen.append((name, number))
            self._changed.notify_all()

    async def wait_for(self, name: str, number: int = 0) -> None:
        async with asyncio.timeout(WAIT_SECONDS), self._changed:
            await self._changed.wait_for(lambda: (name, number) in self.seen)

    def names(self) -> list[str]:
        return [name for name, _ in self.seen]


# --- OffenseIntake ----------------------------------------------------------------------------


class IntakeFakes:
    """Intake activities over in-memory offenses.

    `admit_offenses` reports an offense as changed when its ID is in `open_cases`;
    `find_closed_offenses` reports the open cases whose ID is in `closed`.
    """

    def __init__(self) -> None:
        self.offenses: dict[int, OffenseSnapshot] = {}
        self.open_cases: set[int] = set()
        self.closed: set[int] = set()
        self.pending: list[int] = []
        self.admitted: list[list[int]] = []
        self.started: list[int] = []
        self.closed_records: list[tuple[str, int]] = []

    def put(self, *offenses: OffenseSnapshot) -> None:
        for item in offenses:
            self.offenses[item.offense_id] = item

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.fetch_offense_changes,
            self.admit_offenses,
            self.find_closed_offenses,
            self.next_pending_offenses,
            self.start_case,
            self.close_case,
        ]

    @activity.defn(name=FETCH_OFFENSE_CHANGES)
    async def fetch_offense_changes(
        self, after_time: datetime, after_id: int, limit: int
    ) -> list[OffenseSnapshot]:
        changed = sorted(
            (
                o
                for o in self.offenses.values()
                if (o.last_updated_time, o.offense_id) > (after_time, after_id)
            ),
            key=lambda o: (o.last_updated_time, o.offense_id),
        )
        return changed[:limit]

    @activity.defn(name=ADMIT_OFFENSES)
    async def admit_offenses(self, offenses: list[OffenseSnapshot], now: datetime) -> list[int]:
        self.admitted.append([item.offense_id for item in offenses])
        return [item.offense_id for item in offenses if item.offense_id in self.open_cases]

    @activity.defn(name=FIND_CLOSED_OFFENSES)
    async def find_closed_offenses(self) -> list[int]:
        return sorted(self.open_cases & self.closed)

    @activity.defn(name=NEXT_PENDING_OFFENSES)
    async def next_pending_offenses(self) -> list[int]:
        return list(self.pending)

    @activity.defn(name=START_CASE)
    async def start_case(self, offense_id: int) -> bool:
        self.started.append(offense_id)
        return True

    @activity.defn(name=CLOSE_CASE)
    async def close_case(self, case_id: str, offense_id: int) -> None:
        self.closed_records.append((case_id, offense_id))


@workflow.defn(name="CaseStub")
class CaseStub:
    """Stands in for CaseWorkflow under its ID and records the signals it gets."""

    def __init__(self) -> None:
        self.updates: list[datetime] = []
        self.closed = False

    @workflow.run
    async def run(self) -> list[datetime]:
        await workflow.wait_condition(lambda: self.closed)
        return self.updates

    @workflow.signal(name=OFFENSE_UPDATED)
    def offense_updated(self, last_updated_time: datetime) -> None:
        self.updates.append(last_updated_time)

    @workflow.signal(name=OFFENSE_CLOSED)
    def offense_closed(self) -> None:
        self.closed = True

    @workflow.query(name=CASE_STATE)
    def state(self) -> list[datetime]:
        return self.updates


# --- CaseWorkflow -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageCall:
    """One attempt of TriageStub's scripted activity."""

    evaluation_no: int
    attempt: int
    # Whether the run is the one that retries a run the model's outage ended (D-33).
    retry: bool


type TriageBehavior = Callable[[TriageCall], Awaitable[TriageResult]]


def triage_failure(failure: TriageFailure) -> ApplicationError:
    """What a script raises to end its Triage run with `failure`."""
    return ApplicationError(f"scripted {failure.value}", type=failure.value, non_retryable=True)


@workflow.defn(name=TRIAGE_WORKFLOW)
class TriageStub:
    """Stands in for TriageWorkflow: the scripted activity gives the evaluation's result.

    The activity stands for the agent's model requests, so one that fails for good ends the run
    `failed` with a model error, unless its error names another failure (`triage_failure`).
    """

    @workflow.run
    async def run(self, request: TriageRequest) -> TriageOutcome:
        run_id = workflow.info().workflow_id
        try:
            result = await workflow.execute_activity(
                SCRIPTED_TRIAGE,
                args=[request.case_id, request.evaluation_no],
                result_type=TriageResult,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STUB_RETRY,
            )
        except ActivityError as error:
            failure = _scripted_failure(error)
            exhausted = failure in (TriageFailure.BUDGET_EXHAUSTED, TriageFailure.TIMEOUT)
            return TriageOutcome(
                run_id=run_id,
                status=RunStatus.BUDGET_EXHAUSTED if exhausted else RunStatus.FAILED,
                result=None,
                usage=NO_USAGE,
                error=str(error),
                failure=failure,
            )
        return TriageOutcome(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            result=result,
            usage=result.usage,
            error=None,
            failure=None,
        )


def _scripted_failure(error: ActivityError) -> TriageFailure:
    cause = error.cause
    if isinstance(cause, ApplicationError) and cause.type in {f.value for f in TriageFailure}:
        return TriageFailure(cause.type)
    return TriageFailure.MODEL_ERROR


async def decide_at_once(call: TriageCall) -> TriageResult:
    return triage_result()


@workflow.defn(name=TRIAGE_WORKFLOW)
class BudgetExhaustedTriage:
    """Stands in for a Triage run that ran out of its token budget without a decision."""

    @workflow.run
    async def run(self, request: TriageRequest) -> TriageOutcome:
        return TriageOutcome(
            run_id=workflow.info().workflow_id,
            status=RunStatus.BUDGET_EXHAUSTED,
            result=None,
            usage=NO_USAGE,
            error="UsageLimitExceeded: total_tokens_limit",
            failure=TriageFailure.BUDGET_EXHAUSTED,
        )


@workflow.defn(name=TRIAGE_WORKFLOW)
class CrashingTriage:
    """Stands in for a Triage run whose workflow fails."""

    @workflow.run
    async def run(self, request: TriageRequest) -> TriageOutcome:
        raise ApplicationError("agent misconfigured", non_retryable=True)


class CaseFakes:
    """Case activities for one offense, and the activity TriageStub runs.

    `offense` is what `fetch_offense` returns, so a test changes it to simulate an update;
    `recorded` collects the versions `record_offense_update` got. The SLA deadline follows the
    real rule: offense creation for the first evaluation, the update for later ones, plus `sla`.
    `reevaluation_interval` and `retry_delay` are the settings the workflow reads.
    `triage_behavior` gets each TriageCall; `triage_runs` collects the IDs of the triage runs
    that called it.
    """

    def __init__(
        self,
        offense: OffenseSnapshot,
        *,
        floor_level: Level | None = None,
        sla: timedelta = timedelta(minutes=10),
        reevaluation_interval: timedelta = timedelta(minutes=30),
        retry_delay: timedelta = timedelta(minutes=5),
        triage_behavior: TriageBehavior = decide_at_once,
    ) -> None:
        self.offense = offense
        self.floor_level = floor_level
        self.sla = sla
        self.interval = reevaluation_interval
        self.retry_delay = retry_delay
        self.triage_behavior = triage_behavior
        self.events = Events()
        self.recorded: list[datetime] = []
        self.evaluations: list[tuple[int, datetime]] = []
        self.decisions: list[tuple[int, Level | None, datetime]] = []
        self.closed_records: list[tuple[str, int]] = []
        self.triage_runs: list[str] = []

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.fetch_offense,
            self.record_offense_update,
            self.reevaluation_interval,
            self.enrich_offense,
            self.start_evaluation,
            self.scripted_triage,
            self.triage_retry_delay,
            self.record_decision,
            self.mark_no_ai_decision,
            self.close_case,
        ]

    @activity.defn(name=FETCH_OFFENSE)
    async def fetch_offense(self, offense_id: int) -> OffenseSnapshot:
        return self.offense

    @activity.defn(name=RECORD_OFFENSE_UPDATE)
    async def record_offense_update(self, offense: OffenseSnapshot) -> None:
        self.recorded.append(offense.last_updated_time)
        await self.events.add("recorded", len(self.recorded))

    @activity.defn(name=REEVALUATION_INTERVAL)
    async def reevaluation_interval(self) -> timedelta:
        return self.interval

    @activity.defn(name=TRIAGE_RETRY_DELAY)
    async def triage_retry_delay(self) -> timedelta:
        await self.events.add("retry")
        return self.retry_delay

    @activity.defn(name=ENRICH_OFFENSE)
    async def enrich_offense(self, offense: OffenseSnapshot) -> EnrichmentContext:
        return EnrichmentContext(
            catalog=CatalogContext(rules=[], log_sources=[]),
            critical_asset_hits=[],
            ioc_hits=[],
            entity_resolutions=[],
            floor_level=self.floor_level,
        )

    @activity.defn(name=START_EVALUATION)
    async def start_evaluation(
        self,
        case_id: str,
        evaluation_no: int,
        offense: OffenseSnapshot,
        floor_level: Level | None,
        workflow_id: str,
        run_id: str,
    ) -> datetime:
        start = offense.start_time if evaluation_no == 1 else offense.last_updated_time
        self.evaluations.append((evaluation_no, offense.last_updated_time))
        await self.events.add("evaluation", evaluation_no)
        return start + self.sla

    @activity.defn(name=SCRIPTED_TRIAGE)
    async def scripted_triage(self, case_id: str, evaluation_no: int) -> TriageResult:
        info = activity.info()
        run_id = info.workflow_id or ""
        if run_id not in self.triage_runs:
            self.triage_runs.append(run_id)
        await self.events.add("triage", evaluation_no)
        call = TriageCall(
            evaluation_no=evaluation_no, attempt=info.attempt, retry=run_id.endswith("-retry")
        )
        return await self.triage_behavior(call)

    @activity.defn(name=RECORD_DECISION)
    async def record_decision(
        self,
        case_id: str,
        evaluation_no: int,
        result: TriageResult,
        floor_level: Level | None,
        decided_at: datetime,
    ) -> Level:
        self.decisions.append((evaluation_no, floor_level, decided_at))
        await self.events.add("decided", evaluation_no)
        return result.ai_level

    @activity.defn(name=MARK_NO_AI_DECISION)
    async def mark_no_ai_decision(self, case_id: str, evaluation_no: int) -> None:
        await self.events.add("no_ai_decision", evaluation_no)

    @activity.defn(name=CLOSE_CASE)
    async def close_case(self, case_id: str, offense_id: int) -> None:
        self.closed_records.append((case_id, offense_id))
        await self.events.add("closed")


# --- TriageWorkflow ---------------------------------------------------------------------------

# The activities ScriptedAgent runs for each agent run: a model request and, if asked, a tool
# call before it. They have the names TemporalDurability gives the real agent's, because
# TriageWorkflow tells a failed model request from a failed tool call by the name.
AGENT_STEP = "agent__triage__model_request"
TOOL_STEP = "agent__triage__toolset__gateway-qradar-triage-read__call_tool"

# Gets the run ID and the activity attempt.
type AgentStepBehavior = Callable[[str, int], Awaitable[TriageResult]]
type ToolStepBehavior = Callable[[str, int], Awaitable[None]]


async def answer_at_once(run_id: str, attempt: int) -> TriageResult:
    return triage_result()


async def tool_answers(run_id: str, attempt: int) -> None:
    return None


@dataclass(frozen=True)
class AgentReport:
    """What an agent run reports (`TriageRunReport`)."""

    status: RunStatus
    result: TriageResult | None
    usage: Usage
    error: str | None


class ScriptedAgent:
    """Stands in for the Triage agent in workflow code: one activity plays the script, as the
    agent's model requests would, after a tool call when `tool_call` is set. Records the nonce
    and task of every run."""

    def __init__(self, *, tool_call: bool = False) -> None:
        self.tool_call = tool_call
        self.nonces: list[str] = []
        self.tasks: list[AgentTask] = []

    async def __call__(
        self,
        task: AgentTask,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        nonce: str,
    ) -> AgentReport:
        if not workflow.unsafe.is_replaying():
            self.nonces.append(nonce)
            self.tasks.append(task)
        if self.tool_call:
            await workflow.execute_activity(
                TOOL_STEP,
                task.task_id,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.ABANDON,
            )
        result = await workflow.execute_activity(
            AGENT_STEP,
            task.task_id,
            result_type=TriageResult,
            start_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(maximum_attempts=3),
            cancellation_type=workflow.ActivityCancellationType.ABANDON,
        )
        return AgentReport(
            status=RunStatus.COMPLETED, result=result, usage=result.usage, error=None
        )


def agent_task(run_id: str, *, budget_seconds: int = 180) -> AgentTask:
    return AgentTask(
        task_id=run_id,
        parent_run_id="case-run-1",
        case_id="case-101",
        agent_id="triage",
        agent_version="1.0.0",
        objective="Triage QRadar offense 101 (evaluation 1).",
        context_refs=[],
        time_window=TimeWindow(
            start=datetime(2026, 10, 3, 9, 0, tzinfo=UTC),
            end=datetime(2026, 10, 3, 10, 0, tzinfo=UTC),
        ),
        budget=Budget(tokens=60000, tool_calls=12, seconds=budget_seconds),
    )


class TriageFakes:
    """The run record activities of TriageWorkflow and the agent's scripted steps."""

    def __init__(
        self,
        *,
        budget_seconds: int = 180,
        step: AgentStepBehavior = answer_at_once,
        tool: ToolStepBehavior = tool_answers,
    ) -> None:
        self.budget_seconds = budget_seconds
        self.step = step
        self.tool = tool
        self.events = Events()
        self.begun: list[tuple[str, str, int, str, int]] = []
        self.finished: list[tuple[str, RunStatus, TriageResult | None, str | None]] = []

    def activities(self) -> list[Callable[..., object]]:
        return [self.begin_triage_run, self.finish_triage_run, self.agent_step, self.tool_step]

    @activity.defn(name=BEGIN_TRIAGE_RUN)
    async def begin_triage_run(
        self,
        run_id: str,
        case_id: str,
        evaluation_no: int,
        parent_run_id: str,
        offense: OffenseSnapshot,
    ) -> tuple[AgentTask, str]:
        self.begun.append((run_id, case_id, evaluation_no, parent_run_id, offense.offense_id))
        return agent_task(run_id, budget_seconds=self.budget_seconds), "0123456789abcdef"

    @activity.defn(name=FINISH_TRIAGE_RUN)
    async def finish_triage_run(
        self,
        run_id: str,
        status: RunStatus,
        result: TriageResult | None,
        usage: Usage,
        error: str | None,
    ) -> None:
        self.finished.append((run_id, status, result, error))
        await self.events.add("finished")

    @activity.defn(name=AGENT_STEP)
    async def agent_step(self, run_id: str) -> TriageResult:
        await self.events.add("step")
        return await self.step(run_id, activity.info().attempt)

    @activity.defn(name=TOOL_STEP)
    async def tool_step(self, run_id: str) -> None:
        await self.events.add("tool")
        await self.tool(run_id, activity.info().attempt)
