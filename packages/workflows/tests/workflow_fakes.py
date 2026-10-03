"""Fake activities, a stand-in case workflow and synthetic payloads for the workflow tests.

The fakes are registered under the real activity names, so the workflows run unchanged. IPs are
from the RFC 5737 ranges.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta

from temporalio import activity, workflow

from ais0c_contracts import (
    CaseVerdict,
    CatalogContext,
    Confidence,
    EnrichmentContext,
    Level,
    OffenseSnapshot,
    RunStatus,
    TriageResult,
    Usage,
)
from ais0c_workflows.names import (
    ADMIT_OFFENSES,
    CASE_STATE,
    CLOSE_CASE,
    ENRICH_OFFENSE,
    FETCH_OFFENSE,
    FETCH_OFFENSE_CHANGES,
    FIND_CLOSED_OFFENSES,
    MARK_NO_AI_DECISION,
    NEXT_PENDING_OFFENSES,
    OFFENSE_CLOSED,
    OFFENSE_UPDATED,
    RECORD_DECISION,
    START_CASE,
    START_EVALUATION,
    TRIAGE,
)

WAIT_SECONDS = 10


def offense(
    offense_id: int,
    *,
    start: datetime,
    updated: datetime | None = None,
    rule_ids: Sequence[int] = (100201,),
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
        event_count=12,
        log_source_ids=[112],
        source_ips=["203.0.113.7"],
        destination_ips=["198.51.100.15"],
        usernames=[],
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

type TriageBehavior = Callable[[int, int], Awaitable[TriageResult]]


async def decide_at_once(evaluation_no: int, attempt: int) -> TriageResult:
    return triage_result()


class CaseFakes:
    """Case activities for one offense.

    `offense` is what `fetch_offense` returns, so a test changes it to simulate an update. The
    SLA deadline follows the real rule: offense creation for the first evaluation, the update
    for later ones, plus `sla`. `triage_behavior` gets the evaluation and attempt numbers.
    """

    def __init__(
        self,
        offense: OffenseSnapshot,
        *,
        floor_level: Level | None = None,
        sla: timedelta = timedelta(minutes=10),
        triage_behavior: TriageBehavior = decide_at_once,
    ) -> None:
        self.offense = offense
        self.floor_level = floor_level
        self.sla = sla
        self.triage_behavior = triage_behavior
        self.events = Events()
        self.evaluations: list[tuple[int, datetime]] = []
        self.decisions: list[tuple[int, Level | None, datetime]] = []
        self.closed_records: list[tuple[str, int]] = []

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.fetch_offense,
            self.enrich_offense,
            self.start_evaluation,
            self.triage,
            self.record_decision,
            self.mark_no_ai_decision,
            self.close_case,
        ]

    @activity.defn(name=FETCH_OFFENSE)
    async def fetch_offense(self, offense_id: int) -> OffenseSnapshot:
        return self.offense

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

    @activity.defn(name=TRIAGE)
    async def triage(
        self,
        case_id: str,
        evaluation_no: int,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
    ) -> TriageResult:
        await self.events.add("triage", evaluation_no)
        return await self.triage_behavior(evaluation_no, activity.info().attempt)

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
