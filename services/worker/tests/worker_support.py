"""Support for the case worker tests: synthetic payloads, a scripted Triage step and a running
platform (Temporal test server, PostgreSQL, the real workflows and activities, a fake offense
source).

IPs are from the RFC 5737 ranges.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment

from ais0c_activities import CaseSettings, FakeOffenseSource, SessionFactory
from ais0c_contracts import (
    CaseVerdict,
    Confidence,
    EnrichmentContext,
    Level,
    OffenseSnapshot,
    RunStatus,
    TriageResult,
    Usage,
)
from ais0c_storage.models import CaseRow, OffenseSeenRow
from ais0c_storage.repositories import get_case, get_offense_seen
from ais0c_worker import build_case_worker
from ais0c_workflows import IntakeCheckpoint, OffenseIntake
from ais0c_workflows.names import CASE_TASK_QUEUE

WAIT_SECONDS = 15


def offense(
    offense_id: int,
    *,
    start: datetime,
    updated: datetime | None = None,
    rule_ids: Sequence[int] = (100201,),
    destination_ips: Sequence[str] = ("198.51.100.15",),
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
        destination_ips=list(destination_ips),
        usernames=[],
    )


def triage_result(ai_level: Level = Level.MEDIUM) -> TriageResult:
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


# Gets the offense ID, the evaluation number and the activity attempt.
type TriageScript = Callable[[int, int, int], Awaitable[TriageResult]]


async def decide_at_once(offense_id: int, evaluation_no: int, attempt: int) -> TriageResult:
    return triage_result()


class ScriptedTriage:
    """A `TriageRunner` whose answers the test decides; records every call."""

    def __init__(self, script: TriageScript = decide_at_once) -> None:
        self.script = script
        self.calls: list[tuple[str, int]] = []

    async def triage(
        self,
        *,
        case_id: str,
        evaluation_no: int,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
    ) -> TriageResult:
        self.calls.append((case_id, evaluation_no))
        return await self.script(offense.offense_id, evaluation_no, activity.info().attempt)


async def eventually[T](check: Callable[[], Awaitable[T | None]]) -> T:
    """Poll `check` until it returns something other than None."""
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            value = await check()
            if value is not None:
                return value
            await asyncio.sleep(0.05)


class Platform:
    def __init__(
        self,
        env: WorkflowEnvironment,
        sessions: SessionFactory,
        source: FakeOffenseSource,
        triage: ScriptedTriage,
    ) -> None:
        self.env = env
        self.sessions = sessions
        self.source = source
        self.triage = triage
        self.intake_runs: list[WorkflowHandle[OffenseIntake, IntakeCheckpoint]] = []

    async def run_intake(self, seed: IntakeCheckpoint | None = None) -> IntakeCheckpoint:
        """One intake pass, as the Schedule would start it; `seed` stands for the previous
        run's result."""
        handle = await self.env.client.start_workflow(
            OffenseIntake.run,
            seed,
            id=f"offense-intake-{len(self.intake_runs) + 1}",
            task_queue=CASE_TASK_QUEUE,
        )
        self.intake_runs.append(handle)
        return await handle.result()

    async def seen(self, offense_id: int) -> OffenseSeenRow | None:
        async with self.sessions() as session:
            return await get_offense_seen(session, offense_id)

    async def case(self, case_id: str) -> CaseRow | None:
        async with self.sessions() as session:
            return await get_case(session, case_id)

    async def case_when(self, case_id: str, check: Callable[[CaseRow], bool]) -> CaseRow:
        """The case row once `check` holds for it."""

        async def matching() -> CaseRow | None:
            row = await self.case(case_id)
            return row if row is not None and check(row) else None

        return await eventually(matching)


@asynccontextmanager
async def running_platform(
    env: WorkflowEnvironment,
    sessions: SessionFactory,
    *,
    settings: CaseSettings | None = None,
    triage: ScriptedTriage | None = None,
) -> AsyncIterator[Platform]:
    platform = Platform(env, sessions, FakeOffenseSource(), triage or ScriptedTriage())
    worker = build_case_worker(
        env.client,
        sessions=sessions,
        source=platform.source,
        triage=platform.triage,
        settings=settings or CaseSettings(),
    )
    async with worker:
        yield platform
