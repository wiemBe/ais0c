"""Temporal activities: enrichment, analytics and grouping; they call agents and deterministic jobs.

No workflow logic. Activities are registered under the names in `ais0c_activities.names`, which
the workflows use to call them. The Triage agent's model and tool activities come from Pydantic
AI's TemporalDurability (`TriageRuntime`). The Action Executor's note activity
(`NoteActivities`) is not on the case queue; no workflow calls it before T-026.
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
from ais0c_activities.gateway import (
    GatewayProfile,
    GatewayTool,
    SystemRun,
    SystemRunError,
    system_run,
)
from ais0c_activities.gateway_source import (
    INTAKE_CONTEXT,
    SOURCE_AGENT_ID,
    GatewayOffenseSource,
    OffenseSourceError,
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
from ais0c_activities.model_release import (
    ModelReleaseChange,
    ModelReleaseError,
    compare_model_releases,
    load_model_releases,
    model_release_changes,
    parse_model_releases,
)
from ais0c_activities.note import (
    NOTE_PROFILE,
    WRITE_OFFENSE_NOTE,
    GatewayOffenseNotes,
    NoteActivities,
    NoteGatewayClient,
    NoteRuntime,
    NoteToolset,
    load_note_runtime,
)
from ais0c_activities.offense_source import FakeOffenseSource, OffenseSource
from ais0c_activities.priority import pre_priority
from ais0c_activities.runtime import CaseRuntime, RuntimeConfigError, load_case_runtime
from ais0c_activities.settings import CaseSettings
from ais0c_activities.triage import TriageRunActivities, TriageRuntime
from ais0c_contracts import ModelRelease


def case_queue_activities(
    *,
    client: Client,
    sessions: SessionFactory,
    source: OffenseSource,
    triage: TriageRuntime,
    settings: CaseSettings,
    ioc_matcher: IocMatcher | None = None,
) -> list[Callable[..., object]]:
    """Every activity of the `soc-case` task queue, ready to register on a worker: the intake,
    case and Triage run activities and the Triage agent's own."""
    intake = IntakeActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    launcher = CaseLauncher(client=client, sessions=sessions)
    case = CaseActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    runs = TriageRunActivities(sessions=sessions, runtime=triage)
    return [
        *intake.activities(),
        launcher.start_case,
        *case.activities(),
        *runs.activities(),
        *triage.temporal_activities,
    ]


__all__ = [
    "GROUP_WINDOW",
    "INTAKE_CONTEXT",
    "NOTE_PROFILE",
    "RATE_WINDOW",
    "SOURCE_AGENT_ID",
    "WRITE_OFFENSE_NOTE",
    "CaseActivities",
    "CaseLauncher",
    "CaseRuntime",
    "CaseSettings",
    "FakeOffenseSource",
    "GatewayOffenseNotes",
    "GatewayOffenseSource",
    "GatewayProfile",
    "GatewayTool",
    "GroupState",
    "GroupingDecision",
    "GroupingOutcome",
    "IntakeActivities",
    "IocMatcher",
    # services/worker imports only workflows and activities (docs/impl/repo-structure.md).
    "ModelRelease",
    "ModelReleaseChange",
    "ModelReleaseError",
    "NoIocMatcher",
    "NoteActivities",
    "NoteGatewayClient",
    "NoteRuntime",
    "NoteToolset",
    "OffenseSource",
    "OffenseSourceError",
    "RuntimeConfigError",
    "SessionFactory",
    "SystemRun",
    "SystemRunError",
    "TriageRunActivities",
    "TriageRuntime",
    "build_enrichment",
    "case_queue_activities",
    "catalog_floor",
    "catalog_mode",
    "compare_model_releases",
    "decide_grouping",
    "floor_level",
    "load_case_runtime",
    "load_model_releases",
    "load_note_runtime",
    "match_critical_assets",
    "model_release_changes",
    "parse_model_releases",
    "pre_priority",
    "rule_set_hash",
    "sla_deadline",
    "system_run",
]
