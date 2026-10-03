"""Temporal activities: enrichment, analytics and grouping; they call agents and deterministic jobs.

No workflow logic. Activities are registered under the names in `ais0c_activities.names`, which
the workflows use to call them.
"""

from collections.abc import Callable

from temporalio.client import Client

from ais0c_activities.case import CaseActivities, sla_deadline
from ais0c_activities.db import SessionFactory
from ais0c_activities.enrichment import (
    IocMatcher,
    NoIocMatcher,
    build_enrichment,
    catalog_floor,
    catalog_mode,
    floor_level,
    match_critical_assets,
)
from ais0c_activities.grouping import (
    GROUP_WINDOW,
    RATE_WINDOW,
    GroupingDecision,
    GroupingOutcome,
    GroupState,
    decide_grouping,
    rule_set_hash,
)
from ais0c_activities.intake import CaseLauncher, IntakeActivities
from ais0c_activities.offense_source import FakeOffenseSource, OffenseSource
from ais0c_activities.priority import pre_priority
from ais0c_activities.settings import CaseSettings
from ais0c_activities.triage import TriageRunner


def case_queue_activities(
    *,
    client: Client,
    sessions: SessionFactory,
    source: OffenseSource,
    triage: TriageRunner,
    settings: CaseSettings,
    ioc_matcher: IocMatcher | None = None,
) -> list[Callable[..., object]]:
    """Every activity of the `soc-case` task queue, ready to register on a worker."""
    intake = IntakeActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    launcher = CaseLauncher(client=client, sessions=sessions)
    case = CaseActivities(
        sessions=sessions,
        source=source,
        triage=triage,
        settings=settings,
        ioc_matcher=ioc_matcher,
    )
    return [*intake.activities(), launcher.start_case, *case.activities()]


__all__ = [
    "GROUP_WINDOW",
    "RATE_WINDOW",
    "CaseActivities",
    "CaseLauncher",
    "CaseSettings",
    "FakeOffenseSource",
    "GroupState",
    "GroupingDecision",
    "GroupingOutcome",
    "IntakeActivities",
    "IocMatcher",
    "NoIocMatcher",
    "OffenseSource",
    "SessionFactory",
    "TriageRunner",
    "build_enrichment",
    "case_queue_activities",
    "catalog_floor",
    "catalog_mode",
    "decide_grouping",
    "floor_level",
    "match_critical_assets",
    "pre_priority",
    "rule_set_hash",
    "sla_deadline",
]
