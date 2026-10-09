"""Agents that run in workflow code, through Pydantic AI's TemporalDurability (decision T-02,
architecture §20).

With TemporalDurability an agent's run is workflow code: Pydantic AI turns each model request and
each tool call into an activity, so a worker restart resumes the run from its history. The agent
must be built before the worker starts, outside any workflow, so that its activities can be
registered; the worker builds it and installs its run here.

Workflow modules import this module passed through Temporal's sandbox. The sandbox reloads
workflow code for every run, but a passed-through module is the worker's own, so workflows reach
the agents the worker installed, not a copy.

The workflows package may not import the agents package (docs/impl/repo-structure.md); what it
knows of an agent is the protocols below, written in contract types. Every run gets its run ID
from the workflow (decision T-29) and its `untrusted_*` nonce from the activity that recorded
it.

The chain agents after Triage (T-026) take an input model of this module: what the workflow
hands each one of the earlier agents' results (decision T-45). The models carry structured
fields only; no rationale, summary or hypothesis text reaches the next agent. The evidence the
input cites is read from storage by an activity and handed over beside it as EvidenceRefs.
"""

from collections.abc import Awaitable, Sequence
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from ais0c_contracts import (
    AgentTask,
    Budget,
    CasePlan,
    CaseReport,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    EnrichmentContext,
    EvidenceRef,
    InvestigationResult,
    Level,
    OffenseSnapshot,
    RunStatus,
    SkillRef,
    TriageResult,
    UrgentEvent,
    Usage,
    VerificationResult,
)
from ais0c_workflows.group_summary import GroupSummary
from ais0c_workflows.plan import PlanCandidate


class AgentRunReport[ResultT](Protocol):
    """How an agent run ended; `ais0c_agents.AgentRun` has these fields."""

    @property
    def status(self) -> RunStatus: ...

    @property
    def result(self) -> ResultT | None:
        """Set only when the status is `completed`."""
        ...

    @property
    def usage(self) -> Usage: ...

    @property
    def error(self) -> str | None:
        """Why the run did not complete; for logs, never shown to a model."""
        ...


type TriageRunReport = AgentRunReport[TriageResult]


class TriageAgentRun(Protocol):
    """One run of the Triage agent, called from workflow code.

    `run_id` is the run's `agent_runs.run_id`, which every tool call carries; `nonce` is the
    run's `untrusted_*` tag suffix (docs/impl/prompts.md). `group_summary` is set in a group
    case (T-027): the summary of the group `offense` belongs to. The call must be deterministic
    apart from the activities Pydantic AI starts for it.
    """

    def __call__(
        self,
        task: AgentTask,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        run_id: str,
        nonce: str,
        group_summary: GroupSummary | None = None,
    ) -> Awaitable[TriageRunReport]: ...


class AgentKind(StrEnum):
    """The chain agents after Triage; each value is the agent's manifest ID."""

    ORCHESTRATOR = "orchestrator"
    INVESTIGATION = "investigation"
    VERIFICATION = "verification"
    REPORTING = "reporting"


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrchestratorInput(_Input):
    """What the Orchestrator gets (T-45): Triage's decision without its rationale and claims,
    the candidate skills, the plan agents' manifest budgets and the plan budget."""

    agent: Literal[AgentKind.ORCHESTRATOR] = AgentKind.ORCHESTRATOR
    offense: OffenseSnapshot
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    needs_investigation: bool
    investigation_focus: tuple[str, ...]
    data_gaps: tuple[DataGap, ...]
    injection_suspected: bool
    candidates: tuple[PlanCandidate, ...]
    agents: dict[str, Budget]
    plan_budget: Budget


class TelemetrySource(_Input):
    telemetry_class: str
    type_name: str | None
    log_source_ids: tuple[int, ...]
    total: int


class InvestigationInput(_Input):
    """What Investigation gets (T-45): Triage's structured decision, its claims and its
    investigation focus; the claims' evidence comes beside it."""

    agent: Literal[AgentKind.INVESTIGATION] = AgentKind.INVESTIGATION
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    investigation_focus: tuple[str, ...]
    claims: tuple[Claim, ...]
    data_gaps: tuple[DataGap, ...]
    telemetry: tuple[TelemetrySource, ...] | None = None


class VerificationInput(_Input):
    """What Verification gets (T-45): the decision under review and its claims, every one
    critical or none (T-026 criterion 4); the claims' evidence comes beside it."""

    agent: Literal[AgentKind.VERIFICATION] = AgentKind.VERIFICATION
    offense: OffenseSnapshot
    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    claims: tuple[Claim, ...]
    critical: bool


class ReportingInput(_Input):
    """What Reporting gets (T-45): the workflow's decision and notification level, the claims
    Verification did not dispute, Investigation's urgent event candidates and the data gaps;
    the evidence of the claims and the candidates comes beside it."""

    agent: Literal[AgentKind.REPORTING] = AgentKind.REPORTING
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    verdict: CaseVerdict
    confidence: Confidence
    notify_level: Level
    claims: tuple[Claim, ...]
    urgent_event_candidates: tuple[UrgentEvent, ...]
    data_gaps: tuple[DataGap, ...]


class ChainAgentRun[InputT, ResultT](Protocol):
    """One run of a chain agent, called from AgentWorkflow's workflow code.

    `task` is the run's AgentTask: its objective, window and budget are the plan step's for a
    plan agent. `evidence` holds the EvidenceRefs of `task.context_refs`, in that order, except
    any storage no longer has; `skill` is the skill the run may use, already checked by the
    activity that recorded the run, or None. `run_id` and `nonce` as for Triage.
    """

    def __call__(
        self,
        task: AgentTask,
        inputs: InputT,
        *,
        evidence: Sequence[EvidenceRef],
        skill: SkillRef | None,
        run_id: str,
        nonce: str,
    ) -> Awaitable[AgentRunReport[ResultT]]: ...


type OrchestratorAgentRun = ChainAgentRun[OrchestratorInput, CasePlan]
type InvestigationAgentRun = ChainAgentRun[InvestigationInput, InvestigationResult]
type VerificationAgentRun = ChainAgentRun[VerificationInput, VerificationResult]
type ReportingAgentRun = ChainAgentRun[ReportingInput, CaseReport]


_triage_agent: TriageAgentRun | None = None
_orchestrator_agent: OrchestratorAgentRun | None = None
_investigation_agent: InvestigationAgentRun | None = None
_verification_agent: VerificationAgentRun | None = None
_reporting_agent: ReportingAgentRun | None = None


def install_triage_agent(run: TriageAgentRun) -> None:
    """Make `run` the Triage agent of every TriageWorkflow this process executes.

    Called by the worker before it starts; a later call replaces the agent.
    """
    global _triage_agent
    _triage_agent = run


def install_chain_agents(
    *,
    orchestrator: OrchestratorAgentRun,
    investigation: InvestigationAgentRun,
    verification: VerificationAgentRun,
    reporting: ReportingAgentRun,
) -> None:
    """Make these the chain agents of every AgentWorkflow this process executes.

    Called by the worker before it starts; a later call replaces them.
    """
    global _orchestrator_agent, _investigation_agent, _verification_agent, _reporting_agent
    _orchestrator_agent = orchestrator
    _investigation_agent = investigation
    _verification_agent = verification
    _reporting_agent = reporting


def triage_agent() -> TriageAgentRun:
    """The installed Triage agent.

    Raises RuntimeError when none is installed: the worker is misconfigured, and the workflow
    task fails until a worker with the agent picks it up.
    """
    if _triage_agent is None:
        raise RuntimeError("no Triage agent is installed in this worker")
    return _triage_agent


def orchestrator_agent() -> OrchestratorAgentRun:
    """The installed Orchestrator agent; RuntimeError as for `triage_agent`."""
    if _orchestrator_agent is None:
        raise RuntimeError("no Orchestrator agent is installed in this worker")
    return _orchestrator_agent


def investigation_agent() -> InvestigationAgentRun:
    """The installed Investigation agent; RuntimeError as for `triage_agent`."""
    if _investigation_agent is None:
        raise RuntimeError("no Investigation agent is installed in this worker")
    return _investigation_agent


def verification_agent() -> VerificationAgentRun:
    """The installed Verification agent; RuntimeError as for `triage_agent`."""
    if _verification_agent is None:
        raise RuntimeError("no Verification agent is installed in this worker")
    return _verification_agent


def reporting_agent() -> ReportingAgentRun:
    """The installed Reporting agent; RuntimeError as for `triage_agent`."""
    if _reporting_agent is None:
        raise RuntimeError("no Reporting agent is installed in this worker")
    return _reporting_agent
