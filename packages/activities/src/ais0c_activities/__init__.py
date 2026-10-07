"""Temporal activities: enrichment, analytics and grouping; they call agents and deterministic jobs.

No workflow logic. Activities are registered under the names in `ais0c_activities.names`, which
the workflows use to call them. The agents' model and tool activities come from Pydantic AI's
TemporalDurability (`TriageRuntime`, and `ChainRuntime` for the Orchestrator, Investigation,
Verification and Reporting). The Action Executor's note and e-mail activities
(`NoteActivities`, `EmailActivities`) run in their own process on the `soc-executor` task queue,
from `ExecutorRuntime` (T-33 (1), T-045); the case queue has the rest, including `case_url`. The
`soc-batch` task queue has KnowledgeSync's catalog sync (`CatalogSyncActivities`,
`BatchRuntime.activities`). The case of an offense group in storm (`GroupCaseActivities`,
T-027) runs on the case queue too.
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
from ais0c_activities.executor_runtime import ExecutorRuntime, load_executor_runtime
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
from ais0c_activities.group_case import COUNTED_STATUSES, GroupCaseActivities, GroupCaseState
from ais0c_activities.group_values import NOVELTY_KINDS, novelty_values, offense_values, unseen
from ais0c_activities.grouping import (
    GROUP_WINDOW,
    RATE_WINDOW,
    SAMPLE_ONE_IN,
    GroupingDecision,
    GroupingOutcome,
    GroupState,
    decide_grouping,
    rule_set_hash,
    sample_chosen,
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
from ais0c_activities.names import SEND_EMAIL, WRITE_OFFENSE_NOTE
from ais0c_activities.note import (
    NOTE_PROFILE,
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
from ais0c_activities.triage import (
    TriageRunActivities,
    TriageRuntime,
    evaluation_window,
    group_objective,
)
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
    case, group case, chain and agent run activities and the agents' own."""
    intake = IntakeActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    launcher = CaseLauncher(client=client, sessions=sessions)
    case = CaseActivities(
        sessions=sessions, source=source, settings=settings, ioc_matcher=ioc_matcher
    )
    groups = GroupCaseActivities(sessions=sessions, settings=settings, ioc_matcher=ioc_matcher)
    runs = TriageRunActivities(sessions=sessions, runtime=triage)
    chain_runs = ChainActivities.of(chain, sessions=sessions, settings=settings)
    return [
        *intake.activities(),
        launcher.start_case,
        launcher.wake_group_cases,
        *case.activities(),
        *groups.activities(),
        *runs.activities(),
        *triage.temporal_activities,
        *chain_runs.activities(),
        *chain.temporal_activities,
    ]


__all__ = [
    "CATALOG_SYNC_AGENT_ID",
    "COUNTED_STATUSES",
    "GROUP_WINDOW",
    "INTAKE_CONTEXT",
    "INVENTORY_PROFILE",
    "KNOWLEDGE_SYNC_CONTEXT",
    "NOTE_PROFILE",
    "NOVELTY_KINDS",
    "PLAN_AGENT_ROLES",
    "RATE_WINDOW",
    "SAMPLE_ONE_IN",
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
    "ExecutorRuntime",
    "FakeOffenseSource",
    "GatewayOffenseNotes",
    "GatewayOffenseSource",
    "GatewayProfile",
    "GatewayTool",
    "GroupCaseActivities",
    "GroupCaseState",
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
    "group_objective",
    "investigation_task",
    "load_batch_runtime",
    "load_case_runtime",
    "load_email_runtime",
    "load_executor_runtime",
    "load_model_releases",
    "load_note_runtime",
    "load_smtp_settings",
    "match_critical_assets",
    "model_release_changes",
    "novelty_values",
    "offense_values",
    "orchestrator_task",
    "parse_model_releases",
    "pre_priority",
    "reporting_task",
    "rule_set_hash",
    "sample_applies",
    "sample_chosen",
    "sample_value",
    "sampled",
    "sla_deadline",
    "system_run",
    "unseen",
    "verification_task",
]
