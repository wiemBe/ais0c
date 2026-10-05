"""Activities of `CaseWorkflow` (architecture §6, §9).

One evaluation is: `fetch_offense`, `enrich_offense`, `start_evaluation` (opens the case or
starts the next evaluation and sets the SLA deadline), the Triage run (the child workflow
TriageWorkflow; its activities are in `ais0c_activities.triage`), then `record_decision`, or
`mark_no_ai_decision` when the SLA runs out or triage gives no decision. `close_case` ends the
case when the offense is closed in QRadar.
"""

from collections.abc import Callable
from datetime import datetime

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ais0c_activities.db import SessionFactory
from ais0c_activities.enrichment import IocMatcher, NoIocMatcher, build_enrichment
from ais0c_activities.levels import at_least, max_level
from ais0c_activities.names import (
    CLOSE_CASE,
    ENRICH_OFFENSE,
    FETCH_OFFENSE,
    MARK_NO_AI_DECISION,
    RECORD_DECISION,
    START_EVALUATION,
)
from ais0c_activities.offense_source import OffenseSource
from ais0c_activities.settings import CaseSettings
from ais0c_contracts import CaseSource, EnrichmentContext, Level, OffenseSnapshot, TriageResult
from ais0c_storage.enums import CaseStatus, OffenseStatus
from ais0c_storage.repositories import (
    begin_case_reevaluation,
    create_case,
    get_case,
    get_offense_seen,
    record_case_decision,
    set_case_run_id,
    set_case_status,
    update_offense_seen,
)


def sla_deadline(
    offense: OffenseSnapshot, *, evaluation_no: int, level: Level | None, settings: CaseSettings
) -> datetime:
    """When the AI decision of an evaluation is due (architecture §9, "Ajan SLA'sı").

    The first evaluation is measured from the offense's creation in QRadar, a re-evaluation
    from the update that caused it.
    """
    start = offense.start_time if evaluation_no == 1 else offense.last_updated_time
    return start + settings.sla_for(level)


def _out_of_step(case_id: str, evaluation_no: int) -> ApplicationError:
    return ApplicationError(
        f"case {case_id} is not at evaluation {evaluation_no}",
        type="CaseOutOfStep",
        non_retryable=True,
    )


class CaseActivities:
    def __init__(
        self,
        *,
        sessions: SessionFactory,
        source: OffenseSource,
        settings: CaseSettings,
        ioc_matcher: IocMatcher | None = None,
    ) -> None:
        self._sessions = sessions
        self._source = source
        self._settings = settings
        self._ioc_matcher = NoIocMatcher() if ioc_matcher is None else ioc_matcher

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.fetch_offense,
            self.enrich_offense,
            self.start_evaluation,
            self.record_decision,
            self.mark_no_ai_decision,
            self.close_case,
        ]

    @activity.defn(name=FETCH_OFFENSE)
    async def fetch_offense(self, offense_id: int) -> OffenseSnapshot:
        """The offense as it is now."""
        offense = await self._source.get_offense(offense_id)
        if offense is None:
            raise ApplicationError(
                f"offense {offense_id} is unknown to the offense source",
                type="OffenseNotFound",
                non_retryable=True,
            )
        return offense

    @activity.defn(name=ENRICH_OFFENSE)
    async def enrich_offense(self, offense: OffenseSnapshot) -> EnrichmentContext:
        async with self._sessions() as session:
            seen = await get_offense_seen(session, offense.offense_id)
            return await build_enrichment(
                session,
                offense,
                ioc_matcher=self._ioc_matcher,
                group_id=None if seen is None else seen.group_id,
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
        """Open the case on its first evaluation or start the next one; returns the SLA
        deadline.

        The SLA level is the higher of the floor and the previous decision's notification
        level. An evaluation that has already started keeps its deadline, so a retry changes
        nothing.
        """
        async with self._sessions.begin() as session:
            case = await get_case(session, case_id)
            if case is not None and case.evaluation_no >= evaluation_no:
                return case.sla_due_at
            previous = None if case is None else case.notify_level
            due = sla_deadline(
                offense,
                evaluation_no=evaluation_no,
                level=max_level(floor_level, previous),
                settings=self._settings,
            )
            if case is None:
                case = await create_case(
                    session,
                    case_id=case_id,
                    source=CaseSource.OFFENSE,
                    offense_id=offense.offense_id,
                    sla_due_at=due,
                    workflow_id=workflow_id,
                    run_id=run_id,
                    evaluation_no=evaluation_no,
                )
            else:
                case = await begin_case_reevaluation(session, case_id, sla_due_at=due)
                if case.evaluation_no != evaluation_no:
                    raise _out_of_step(case_id, evaluation_no)
                if case.run_id != run_id:
                    case = await set_case_run_id(session, case_id, run_id)
            return case.sla_due_at

    @activity.defn(name=RECORD_DECISION)
    async def record_decision(
        self,
        case_id: str,
        evaluation_no: int,
        result: TriageResult,
        floor_level: Level | None,
        decided_at: datetime,
    ) -> Level:
        """Store the evaluation's decision; returns its notification level.

        The notification level is max(AI level, floor) (architecture §9): the AI cannot take a
        case below its floor.
        """
        notify_level = at_least(result.ai_level, floor_level)
        async with self._sessions.begin() as session:
            case = await get_case(session, case_id)
            if case is None or case.evaluation_no != evaluation_no:
                raise _out_of_step(case_id, evaluation_no)
            await record_case_decision(
                session,
                case_id,
                verdict=result.verdict,
                confidence=result.confidence,
                ai_level=result.ai_level,
                notify_level=notify_level,
                floor_level=floor_level,
                decided_at=decided_at,
            )
        return notify_level

    @activity.defn(name=MARK_NO_AI_DECISION)
    async def mark_no_ai_decision(self, case_id: str, evaluation_no: int) -> None:
        """The evaluation missed its SLA or triage failed: the case waits for the operator with
        "AI kararı yok" (architecture §9). A decision recorded meanwhile is kept."""
        async with self._sessions.begin() as session:
            case = await get_case(session, case_id)
            if (
                case is not None
                and case.evaluation_no == evaluation_no
                and case.status is CaseStatus.RUNNING
            ):
                await set_case_status(session, case_id, CaseStatus.NO_AI_DECISION)

    @activity.defn(name=CLOSE_CASE)
    async def close_case(self, case_id: str, offense_id: int) -> None:
        """The offense was closed in QRadar: the case becomes `closed`, the offense `done`."""
        async with self._sessions.begin() as session:
            if await get_case(session, case_id) is not None:
                await set_case_status(session, case_id, CaseStatus.CLOSED)
            if await get_offense_seen(session, offense_id) is not None:
                await update_offense_seen(session, offense_id, status=OffenseStatus.DONE)
