"""Activities of `GroupCaseWorkflow`: the case of an offense group in storm (architecture §9,
"Offense gruplama ve fırtına koruması"; T-14, T-22, T-62, task T-027).

- `group_settle_delay`: how long the case waits after the group's storm began before its first
  evaluation, so the whole burst is in its summary (T-62).
- `group_case_state`: the group as its case sees it on every wake-up: the summary of its
  offenses, the offenses it took (`grouped`) and the end of its window. The offenses it took
  are checked against the catalog first (T-30 (3)): one whose rules are all `skip` now becomes
  `skipped`, gets no group note and leaves the summary.
- `enrich_group`: the enrichment of the offense the summary shows, with the Analysis Catalog
  entries of the group's rules and most frequent log sources; its floor is the highest of the
  group's (the task's "grubun en yüksek tabanı").
- `begin_group_evaluation`: opens the group's case (`cases`, source `group`) on its first
  evaluation or starts the next one; returns the SLA deadline.
- `close_group_case`: closes the group and its case once the group's window has ended.

The summary is deterministic: the same records give the same summary. The decision itself is
recorded by the case activities `record_decision` and `mark_no_ai_decision`, under the group
case's ID; the note and the e-mail are the executor's.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ais0c_activities.db import SessionFactory
from ais0c_activities.enrichment import (
    IocMatcher,
    NoIocMatcher,
    build_enrichment,
    catalog_floor,
    catalog_mode,
)
from ais0c_activities.levels import max_level
from ais0c_activities.names import (
    BEGIN_GROUP_EVALUATION,
    CLOSE_GROUP_CASE,
    ENRICH_GROUP,
    GROUP_CASE_STATE,
    GROUP_SETTLE_DELAY,
)
from ais0c_activities.settings import CaseSettings
from ais0c_agents import (
    MAX_GROUP_RULES,
    MAX_GROUP_TOP_VALUES,
    MAX_GROUP_VALUE_LENGTH,
    GroupRule,
    GroupSummary,
    GroupValueCount,
    GroupValues,
)
from ais0c_contracts import (
    CaseSource,
    CatalogContext,
    CatalogMode,
    EnrichmentContext,
    Level,
    OffenseSnapshot,
)
from ais0c_storage.enums import CaseStatus, GroupStatus, GroupValueKind, OffenseStatus
from ais0c_storage.models import OffenseSeenRow
from ais0c_storage.repositories import (
    GroupValueCounts,
    begin_case_reevaluation,
    close_ended_offense_group,
    count_group_values,
    create_case,
    get_case,
    get_catalog_log_sources,
    get_catalog_rules,
    get_offense_group,
    list_group_offenses,
    set_case_run_id,
    set_case_status,
    to_catalog_log_source,
    to_catalog_rule,
    update_offense_group,
    update_offense_seen,
)

# The offenses a group's summary counts: all but those the catalog skips.
COUNTED_STATUSES: Final = frozenset(set(OffenseStatus) - {OffenseStatus.SKIPPED})

type GroupCaseState = tuple[GroupSummary | None, list[int], datetime]
"""The summary (None when the group has taken no offense it still analyzes), the IDs of the
offenses the group took, oldest first, and the end of the group's window."""


def _not_found(group_id: str) -> ApplicationError:
    return ApplicationError(
        f"offense group {group_id} is unknown", type="GroupNotFound", non_retryable=True
    )


class GroupCaseActivities:
    def __init__(
        self,
        *,
        sessions: SessionFactory,
        settings: CaseSettings,
        ioc_matcher: IocMatcher | None = None,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._ioc_matcher = NoIocMatcher() if ioc_matcher is None else ioc_matcher

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.group_settle_delay,
            self.group_case_state,
            self.enrich_group,
            self.begin_group_evaluation,
            self.close_group_case,
        ]

    @activity.defn(name=GROUP_SETTLE_DELAY)
    async def group_settle_delay(self) -> timedelta:
        """How long after the storm began the group is evaluated (`AIS0C_GROUP_SETTLE_MINUTES`,
        T-62)."""
        return self._settings.group_settle

    @activity.defn(name=GROUP_CASE_STATE)
    async def group_case_state(self, group_id: str) -> GroupCaseState:
        """The group now: its summary, the offenses it took and its window's end.

        The offense the summary shows is the oldest the group took: the storm's first, whose
        start opens the evaluation's window.
        """
        async with self._sessions.begin() as session:
            group = await get_offense_group(session, group_id)
            if group is None:
                raise _not_found(group_id)
            offenses = await _check_catalog(session, await list_group_offenses(session, group_id))
            grouped = [row.offense_id for row in offenses if row.status is OffenseStatus.GROUPED]
            counted = [row for row in offenses if row.status in COUNTED_STATUSES]
            summary = (
                await _summary(session, group_id, counted, example=grouped[0]) if grouped else None
            )
            return summary, grouped, group.window_end

    @activity.defn(name=ENRICH_GROUP)
    async def enrich_group(
        self, group_id: str, offense: OffenseSnapshot, summary: GroupSummary
    ) -> EnrichmentContext:
        """The enrichment of `offense`, the offense the summary shows, for the group's case.

        The catalog context adds the entries of the group's rules and most frequent log
        sources; the floor is the highest of the offense's and the group's rules'.
        """
        async with self._sessions() as session:
            base = await build_enrichment(
                session, offense, ioc_matcher=self._ioc_matcher, group_id=group_id
            )
            rules = list(base.catalog.rules)
            known_rules = {rule.rule_id for rule in rules}
            wanted_rules = [rule.rule_id for rule in summary.rules]
            rules += [
                to_catalog_rule(row)
                for row in await get_catalog_rules(session, wanted_rules)
                if row.rule_id not in known_rules
            ]
            log_sources = list(base.catalog.log_sources)
            known_sources = {source.log_source_id for source in log_sources}
            wanted_sources = _ids(item.value for item in summary.log_sources.top)
            log_sources += [
                to_catalog_log_source(row)
                for row in await get_catalog_log_sources(session, wanted_sources)
                if row.log_source_id not in known_sources
            ]
        return EnrichmentContext(
            catalog=CatalogContext(rules=rules, log_sources=log_sources),
            critical_asset_hits=base.critical_asset_hits,
            ioc_hits=base.ioc_hits,
            entity_resolutions=base.entity_resolutions,
            group_id=group_id,
            floor_level=max_level(base.floor_level, catalog_floor(rules)),
        )

    @activity.defn(name=BEGIN_GROUP_EVALUATION)
    async def begin_group_evaluation(
        self,
        case_id: str,
        group_id: str,
        evaluation_no: int,
        floor_level: Level | None,
        sla_start: datetime,
        workflow_id: str,
        run_id: str,
    ) -> datetime:
        """Open the group's case on its first evaluation or start the next one; returns the SLA
        deadline.

        The deadline is `sla_start` (the storm's start for the first evaluation, the
        evaluation's own start for a later one) plus the SLA of the higher of the floor and the
        previous decision's level. The group records its case. A retry changes nothing.
        """
        async with self._sessions.begin() as session:
            case = await get_case(session, case_id)
            if case is not None and case.evaluation_no >= evaluation_no:
                return case.sla_due_at
            previous = None if case is None else case.notify_level
            due = sla_start + self._settings.sla_for(max_level(floor_level, previous))
            if case is None:
                case = await create_case(
                    session,
                    case_id=case_id,
                    source=CaseSource.GROUP,
                    group_id=group_id,
                    sla_due_at=due,
                    workflow_id=workflow_id,
                    run_id=run_id,
                    evaluation_no=evaluation_no,
                )
                await update_offense_group(session, group_id, case_id=case_id)
                return case.sla_due_at
            case = await begin_case_reevaluation(session, case_id, sla_due_at=due)
            if case.evaluation_no != evaluation_no:
                raise ApplicationError(
                    f"case {case_id} is not at evaluation {evaluation_no}",
                    type="CaseOutOfStep",
                    non_retryable=True,
                )
            if case.run_id != run_id:
                case = await set_case_run_id(session, case_id, run_id)
            return case.sla_due_at

    @activity.defn(name=CLOSE_GROUP_CASE)
    async def close_group_case(self, group_id: str, case_id: str, at: datetime) -> datetime | None:
        """Close the group and its case if the group's window ended before `at`; returns None
        when closed, otherwise the window's end, which an offense may have moved."""
        async with self._sessions.begin() as session:
            group = await close_ended_offense_group(session, group_id, at=at)
            if group is None:
                raise _not_found(group_id)
            if group.status is not GroupStatus.CLOSED:
                return group.window_end
            if await get_case(session, case_id) is not None:
                await set_case_status(session, case_id, CaseStatus.CLOSED)
            return None


async def _check_catalog(
    session: AsyncSession, offenses: Sequence[OffenseSeenRow]
) -> list[OffenseSeenRow]:
    """The offenses, those the group took and the catalog skips now recorded as `skipped`."""
    modes: dict[tuple[int, ...], CatalogMode] = {}
    checked: list[OffenseSeenRow] = []
    for row in offenses:
        if row.status is OffenseStatus.GROUPED:
            key = tuple(sorted(set(row.rule_ids)))
            if key not in modes:
                rules = [to_catalog_rule(rule) for rule in await get_catalog_rules(session, key)]
                modes[key] = catalog_mode(key, rules)
            if modes[key] is CatalogMode.SKIP:
                row = await update_offense_seen(
                    session,
                    row.offense_id,
                    status=OffenseStatus.SKIPPED,
                    catalog_mode=CatalogMode.SKIP,
                )
        checked.append(row)
    return checked


async def _summary(
    session: AsyncSession, group_id: str, counted: Sequence[OffenseSeenRow], *, example: int
) -> GroupSummary:
    rule_ids = sorted({rule_id for row in counted for rule_id in row.rule_ids})[:MAX_GROUP_RULES]
    names = {row.rule_id: row.rule_name for row in await get_catalog_rules(session, rule_ids)}
    counts = await count_group_values(
        session, group_id, top=MAX_GROUP_TOP_VALUES, statuses=COUNTED_STATUSES
    )
    return GroupSummary(
        offense_count=len(counted),
        first_seen_at=min(row.first_seen_at for row in counted),
        last_seen_at=max(row.first_seen_at for row in counted),
        example_offense_id=example,
        rules=[GroupRule(rule_id=rule_id, name=_cut(names.get(rule_id))) for rule_id in rule_ids],
        source_ips=_values(counts.get(GroupValueKind.SOURCE_IP)),
        destination_ips=_values(counts.get(GroupValueKind.DESTINATION_IP)),
        usernames=_values(counts.get(GroupValueKind.USERNAME)),
        log_sources=_values(counts.get(GroupValueKind.LOG_SOURCE)),
        categories=_values(counts.get(GroupValueKind.CATEGORY)),
    )


def _values(counts: GroupValueCounts | None) -> GroupValues:
    if counts is None:
        return GroupValues(distinct=0, top=[])
    return GroupValues(
        distinct=counts.distinct,
        top=[
            GroupValueCount(value=value[:MAX_GROUP_VALUE_LENGTH], offenses=offenses)
            for value, offenses in counts.top
        ],
    )


def _cut(name: str | None) -> str | None:
    return None if name is None else name[:MAX_GROUP_VALUE_LENGTH]


def _ids(values: Iterable[str]) -> list[int]:
    return [int(value) for value in values if value.isdigit()]
