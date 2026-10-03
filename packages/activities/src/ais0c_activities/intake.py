"""Activities of `OffenseIntake` (architecture §6, §9).

The workflow pages through changed offenses and hands each page to `admit_offenses`, which
records every offense once and applies the Analysis Catalog filter, the grouping decision and
the pre-priority. `next_pending_offenses` picks the pending offenses that fit under the
concurrent case limit and `start_case` starts their case workflows.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from ais0c_activities.db import SessionFactory
from ais0c_activities.enrichment import (
    IocMatcher,
    NoIocMatcher,
    build_enrichment,
    catalog_floor,
    catalog_mode,
)
from ais0c_activities.grouping import (
    RATE_WINDOW,
    GroupingOutcome,
    GroupState,
    decide_grouping,
    rule_set_hash,
)
from ais0c_activities.names import (
    ADMIT_OFFENSES,
    CASE_TASK_QUEUE,
    CASE_WORKFLOW,
    FETCH_OFFENSE_CHANGES,
    FIND_CLOSED_OFFENSES,
    NEXT_PENDING_OFFENSES,
    START_CASE,
    case_workflow_id,
)
from ais0c_activities.offense_source import OffenseSource
from ais0c_activities.priority import pre_priority
from ais0c_activities.settings import CaseSettings
from ais0c_contracts import CatalogMode, OffenseSnapshot
from ais0c_storage.enums import CaseStatus, GroupStatus, OffenseStatus
from ais0c_storage.models import CaseRow, OffenseSeenRow
from ais0c_storage.repositories import (
    add_offense_seen,
    count_offenses,
    create_offense_group,
    find_offense_group,
    get_offense_seen,
    increment_offense_group,
    list_pending_offenses,
    update_offense_group,
    update_offense_seen,
)

# Offenses that went, or are queued to go, to a full analysis.
FULL_ANALYSIS_STATUSES: Final = frozenset(
    {OffenseStatus.PENDING, OffenseStatus.RUNNING, OffenseStatus.DONE}
)


class IntakeActivities:
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
            self.fetch_offense_changes,
            self.admit_offenses,
            self.find_closed_offenses,
            self.next_pending_offenses,
        ]

    @activity.defn(name=FETCH_OFFENSE_CHANGES)
    async def fetch_offense_changes(
        self, after_time: datetime, after_id: int, limit: int
    ) -> list[OffenseSnapshot]:
        """Open offenses changed after the cursor, oldest first (`OffenseSource`)."""
        return await self._source.changed_offenses(
            after_time=after_time, after_id=after_id, limit=limit
        )

    @activity.defn(name=ADMIT_OFFENSES)
    async def admit_offenses(self, offenses: list[OffenseSnapshot], now: datetime) -> list[int]:
        """Record the offenses; returns the IDs whose open case must evaluate them again.

        A new offense is recorded once: `skipped` when the catalog skips its rules, otherwise
        `pending` for a full analysis or `grouped` (architecture §9). An offense seen before
        only updates its record; it is returned when its case workflow is open, also when this
        version was already reported, because the case ignores versions it has evaluated. Each
        offense is committed on its own, so a retry does not repeat earlier work.
        """
        reevaluate: list[int] = []
        for offense in offenses:
            async with self._sessions.begin() as session:
                if await self._admit(session, offense, now):
                    reevaluate.append(offense.offense_id)
        return reevaluate

    async def _admit(self, session: AsyncSession, offense: OffenseSnapshot, now: datetime) -> bool:
        seen = await get_offense_seen(session, offense.offense_id)
        if seen is None:
            await self._admit_new(session, offense, now)
            return False
        if offense.last_updated_time < seen.last_updated_at:
            return False
        if offense.last_updated_time > seen.last_updated_at:
            await update_offense_seen(
                session,
                offense.offense_id,
                last_updated_at=offense.last_updated_time,
                description=offense.description,
                rule_ids=offense.rule_ids,
            )
        return seen.status is OffenseStatus.RUNNING

    async def _admit_new(
        self, session: AsyncSession, offense: OffenseSnapshot, now: datetime
    ) -> None:
        enrichment = await build_enrichment(session, offense, ioc_matcher=self._ioc_matcher)
        if catalog_mode(offense.rule_ids, enrichment.catalog.rules) is CatalogMode.SKIP:
            await _record(
                session,
                offense,
                now,
                mode=CatalogMode.SKIP,
                priority=0,
                status=OffenseStatus.SKIPPED,
                group_id=None,
            )
            return

        floor = catalog_floor(enrichment.catalog.rules)
        asset_hit = bool(enrichment.critical_asset_hits)
        ioc_hit = bool(enrichment.ioc_hits)
        decision = decide_grouping(
            rule_ids=offense.rule_ids,
            at=now,
            group=await self._group_state(session, rule_set_hash(offense.rule_ids), now),
            critical_asset_hit=asset_hit,
            ioc_hit=ioc_hit,
            catalog_floor=floor,
            full_analyses_per_hour=self._settings.group_full_analyses_per_hour,
        )
        full_analysis = decision.outcome is GroupingOutcome.FULL_ANALYSIS
        inserted = await _record(
            session,
            offense,
            now,
            mode=CatalogMode.ANALYZE,
            priority=pre_priority(
                catalog_floor=floor, critical_asset_hit=asset_hit, ioc_hit=ioc_hit
            ),
            status=OffenseStatus.PENDING if full_analysis else OffenseStatus.GROUPED,
            group_id=decision.group_id,
        )
        if not inserted:
            return
        storm = decision.outcome is GroupingOutcome.START_GROUP_EVALUATION
        if decision.new_group:
            await create_offense_group(
                session,
                group_id=decision.group_id,
                rule_set_hash=decision.rule_set_hash,
                window_start=now,
                window_end=decision.window_end,
                offense_count=1,
                status=GroupStatus.STORM if storm else GroupStatus.OPEN,
            )
            return
        await increment_offense_group(session, decision.group_id, window_end=decision.window_end)
        if storm:
            # Evaluating the group itself is not part of T-010; only the state changes.
            await update_offense_group(session, decision.group_id, status=GroupStatus.STORM)

    async def _group_state(
        self, session: AsyncSession, key: str, now: datetime
    ) -> GroupState | None:
        group = await find_offense_group(session, rule_set_hash=key, at=now)
        if group is None:
            return None
        recent = await count_offenses(
            session,
            statuses=FULL_ANALYSIS_STATUSES,
            group_id=group.group_id,
            first_seen_since=now - RATE_WINDOW,
        )
        return GroupState(
            group_id=group.group_id,
            rule_set_hash=group.rule_set_hash,
            window_end=group.window_end,
            status=group.status,
            full_analyses_last_hour=recent,
        )

    @activity.defn(name=FIND_CLOSED_OFFENSES)
    async def find_closed_offenses(self) -> list[int]:
        """Offenses with an open case workflow that the source reports as closed."""
        async with self._sessions() as session:
            open_ids = list(
                await session.scalars(
                    select(OffenseSeenRow.offense_id)
                    .where(OffenseSeenRow.status == OffenseStatus.RUNNING)
                    .order_by(OffenseSeenRow.offense_id)
                )
            )
        if not open_ids:
            return []
        return sorted(await self._source.closed_offenses(open_ids))

    @activity.defn(name=NEXT_PENDING_OFFENSES)
    async def next_pending_offenses(self) -> list[int]:
        """Pending offenses whose case can start now, highest pre-priority first.

        Only as many as fit under the concurrent case limit. A case counts while it evaluates:
        from its start until it records a decision or misses its SLA. Cases that wait for
        updates of a decided offense do not count.
        """
        async with self._sessions() as session:
            evaluating = await session.scalar(
                select(func.count())
                .select_from(OffenseSeenRow)
                .outerjoin(CaseRow, CaseRow.case_id == OffenseSeenRow.case_id)
                .where(
                    OffenseSeenRow.status == OffenseStatus.RUNNING,
                    # A case that has not opened its row yet is evaluating too.
                    or_(CaseRow.case_id.is_(None), CaseRow.status == CaseStatus.RUNNING),
                )
            )
            room = self._settings.max_concurrent_cases - (evaluating or 0)
            if room <= 0:
                return []
            pending = await list_pending_offenses(session, limit=room)
        return [row.offense_id for row in pending]


async def _record(
    session: AsyncSession,
    offense: OffenseSnapshot,
    now: datetime,
    *,
    mode: CatalogMode,
    priority: int,
    status: OffenseStatus,
    group_id: str | None,
) -> bool:
    """Add the offense to `offenses_seen`; `first_seen_at` is the intake time."""
    return await add_offense_seen(
        session,
        offense_id=offense.offense_id,
        first_seen_at=now,
        last_updated_at=offense.last_updated_time,
        description=offense.description,
        rule_ids=offense.rule_ids,
        catalog_mode=mode,
        pre_priority=priority,
        status=status,
        group_id=group_id,
    )


class CaseLauncher:
    """Starts case workflows; needs a Temporal client that uses the Pydantic data converter."""

    def __init__(self, *, client: Client, sessions: SessionFactory) -> None:
        self._client = client
        self._sessions = sessions

    @activity.defn(name=START_CASE)
    async def start_case(self, offense_id: int) -> bool:
        """Start the offense's case workflow; False if one with its ID already exists.

        The ID is `case-<offense_id>` and is never reused, not even after the case closed
        (architecture §20). The offense then moves from `pending` to `running`.
        """
        case_id = case_workflow_id(offense_id)
        try:
            await self._client.start_workflow(
                CASE_WORKFLOW,
                offense_id,
                id=case_id,
                task_queue=CASE_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
            started = True
        except WorkflowAlreadyStartedError:
            activity.logger.warning("case workflow %s already exists", case_id)
            started = False
        async with self._sessions.begin() as session:
            seen = await get_offense_seen(session, offense_id)
            if seen is not None and seen.status is OffenseStatus.PENDING:
                await update_offense_seen(
                    session, offense_id, status=OffenseStatus.RUNNING, case_id=case_id
                )
        return started
