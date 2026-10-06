"""Temporal activities: enrichment, analytics and grouping; they call agents and deterministic jobs.

No workflow logic. Activities are registered under the names in `ais0c_activities.names`, which
the workflows use to call them. The agents' model and tool activities come from Pydantic AI's
TemporalDurability (`TriageRuntime`, and `ChainRuntime` for the Orchestrator, Investigation,
Verification and Reporting). The Action Executor's note and e-mail activities
(`NoteActivities`, `EmailActivities`) are not on the case queue; no workflow calls them before
T-045. The `soc-batch` task queue has KnowledgeSync's catalog sync (`CatalogSyncActivities`,
`BatchRuntime.activities`).
"""

from collections.abc import Callable

from temporalio.client import Client

from ais0c_activities.agent_runtimes import (
    ChainAgent,
    ChainRuntime,
    InvestigationRuntime,
    OrchestratorRuntime,
    ReportingRuntime,
    VerificationRuntime,
    investigation_task,
    orchestrator_task,
    reporting_task,
    verification_task,
)
from ais0c_activities.case import CaseActivities, sla_deadline
from ais0c_activities.catalog import (
    CATALOG_SYNC_AGENT_ID,
    INVENTORY_PROFILE,
    KNOWLEDGE_SYNC_CONTEXT,
    CatalogSyncActivities,
)
from ais0c_activities.chain import ChainActivities
from ais0c_activities.db import SessionFactory
from ais0c_activities.email import (
    SEND_EMAIL,
    EmailActivities,
    EmailRuntime,
    load_email_runtime,
    load_smtp_settings,
)
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
from ais0c_activities.qa import sample_applies, sample_value, sampled
from ais0c_activities.runtime import (
    BatchRuntime,
    CaseRuntime,
    RuntimeConfigError,
    load_batch_runtime,
    load_case_runtime,
)
from ais0c_activities.settings import CaseSettings
from ais0c_activities.skills import PLAN_AGENT_ROLES, candidates, check_skill
from ais0c_activities.triage import TriageRunActivities, TriageRuntime, evaluation_window
from ais0c_contracts import ModelRelease


def case_queue_activities(
    *,
    client: Client,
    sessions: SessionFactory,
    source: OffenseSource,
    triage: TriageRuntime,
    chain: ChainRuntime,
    settings: CaseSettings,
    ioc_matcher: IocMatcher | None = None,
) -> list[Callable[..., object]]:
    """Every activity of the `soc-case` task queue, ready to register on a worker: the intake,
    case, chain and agent run activities and the agents' own."""
    intake = IntakeActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    launcher = CaseLauncher(client=client, sessions=sessions)
    case = CaseActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    runs = TriageRunActivities(sessions=sessions, runtime=triage)
    chain_runs = ChainActivities.of(chain, sessions=sessions, settings=settings)
    return [
        *intake.activities(),
        launcher.start_case,
        *case.activities(),
        *runs.activities(),
        *triage.temporal_activities,
        *chain_runs.activities(),
        *chain.temporal_activities,
    ]


__all__ = [
    "CATALOG_SYNC_AGENT_ID",
    "GROUP_WINDOW",
    "INTAKE_CONTEXT",
    "INVENTORY_PROFILE",
    "KNOWLEDGE_SYNC_CONTEXT",
    "NOTE_PROFILE",
    "PLAN_AGENT_ROLES",
    "RATE_WINDOW",
    "SEND_EMAIL",
    "SOURCE_AGENT_ID",
    "WRITE_OFFENSE_NOTE",
    "BatchRuntime",
    "CaseActivities",
    "CaseLauncher",
    "CaseRuntime",
    "CaseSettings",
    "CatalogSyncActivities",
    "ChainActivities",
    "ChainAgent",
    "ChainRuntime",
    "EmailActivities",
    "EmailRuntime",
    "FakeOffenseSource",
    "GatewayOffenseNotes",
    "GatewayOffenseSource",
    "GatewayProfile",
    "GatewayTool",
    "GroupState",
    "GroupingDecision",
    "GroupingOutcome",
    "IntakeActivities",
    "InvestigationRuntime",
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
    "OrchestratorRuntime",
    "ReportingRuntime",
    "RuntimeConfigError",
    "SessionFactory",
    "SystemRun",
    "SystemRunError",
    "TriageRunActivities",
    "TriageRuntime",
    "VerificationRuntime",
    "build_enrichment",
    "candidates",
    "case_queue_activities",
    "catalog_floor",
    "catalog_mode",
    "check_skill",
    "compare_model_releases",
    "decide_grouping",
    "evaluation_window",
    "floor_level",
    "investigation_task",
    "load_batch_runtime",
    "load_case_runtime",
    "load_email_runtime",
    "load_model_releases",
    "load_note_runtime",
    "load_smtp_settings",
    "match_critical_assets",
    "model_release_changes",
    "orchestrator_task",
    "parse_model_releases",
    "pre_priority",
    "reporting_task",
    "rule_set_hash",
    "sample_applies",
    "sample_value",
    "sampled",
    "sla_deadline",
    "system_run",
    "verification_task",
]
