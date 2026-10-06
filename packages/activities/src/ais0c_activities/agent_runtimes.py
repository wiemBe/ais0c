"""The chain agents after Triage under Temporal (architecture §7, §20; decisions T-02, T-45;
T-026): the Orchestrator, Investigation, Verification and Reporting.

Each runtime holds its agent, built once with Pydantic AI's TemporalDurability like Triage's
(`ais0c_activities.triage`, the same activity settings). Its `run` is called from AgentWorkflow's
workflow code with what CaseWorkflow handed over (`ais0c_workflows.agent_runtime`): structured
fields of the earlier agents' results, never their free text, and the EvidenceRefs those fields
cite. `run` builds the agent's task from them and runs the agent as the run `run_id`, which every
tool call carries (T-29).

The builders fit the input to the agent's limits instead of letting the task refuse it: a claim
whose evidence storage no longer has is left out (Verification keeps it: its code contests such a
claim itself), and claims past an agent's claim or evidence limit are left out in order. The
builders are pure; the skill registry is the one the worker loaded at start-up and never
changes while it runs.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, Self

from pydantic_ai import Agent
from pydantic_ai.durable_exec.temporal import TemporalDurability
from pydantic_ai.models import Model
from temporalio import workflow

from ais0c_activities.skills import candidate_skill, skill_input
from ais0c_activities.triage import MODEL_ACTIVITY, TOOL_ACTIVITY
from ais0c_agents import (
    AgentManifest,
    AgentRun,
    Budgets,
    CaseDecision,
    GatewayClient,
    InvestigationAgent,
    InvestigationTask,
    InvestigationTriage,
    OrchestratorAgent,
    OrchestratorTask,
    PlanAgent,
    PromptTemplate,
    ReportingAgent,
    ReportingTask,
    ReviewedClaim,
    ReviewedDecision,
    RunDeps,
    SkillInput,
    ToolsetProfile,
    TriageDecision,
    VerificationAgent,
    VerificationTask,
    build_investigation_agent,
    build_orchestrator_agent,
    build_reporting_agent,
    build_verification_agent,
)
from ais0c_agents import investigation as investigation_limits
from ais0c_agents import reporting as reporting_limits
from ais0c_agents import verification as verification_limits
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
    ModelRelease,
    OffenseSnapshot,
    SkillRef,
    TimeWindow,
    UrgentEvent,
    VerificationResult,
)
from ais0c_knowledge.skills import Mode, SkillRegistry

# What a Triage investigation focus may hold (TriageResult and the agents' inputs agree).
MAX_FOCUS: Final = 5


# --- what the workflow hands over (ais0c_workflows.agent_runtime) ----------------------------
# The workflows package's input models have these attributes; they are read, never changed.


class CandidateInput(Protocol):
    @property
    def agent_id(self) -> str: ...
    @property
    def skill(self) -> SkillRef: ...


class OrchestratorInputs(Protocol):
    @property
    def offense(self) -> OffenseSnapshot: ...
    @property
    def verdict(self) -> CaseVerdict: ...
    @property
    def confidence(self) -> Confidence: ...
    @property
    def ai_level(self) -> Level: ...
    @property
    def needs_investigation(self) -> bool: ...
    @property
    def investigation_focus(self) -> Sequence[str]: ...
    @property
    def data_gaps(self) -> Sequence[DataGap]: ...
    @property
    def injection_suspected(self) -> bool: ...
    @property
    def candidates(self) -> Sequence[CandidateInput]: ...
    @property
    def agents(self) -> Mapping[str, Budget]: ...
    @property
    def plan_budget(self) -> Budget: ...


class InvestigationInputs(Protocol):
    @property
    def offense(self) -> OffenseSnapshot: ...
    @property
    def enrichment(self) -> EnrichmentContext: ...
    @property
    def verdict(self) -> CaseVerdict: ...
    @property
    def confidence(self) -> Confidence: ...
    @property
    def ai_level(self) -> Level: ...
    @property
    def investigation_focus(self) -> Sequence[str]: ...
    @property
    def claims(self) -> Sequence[Claim]: ...
    @property
    def data_gaps(self) -> Sequence[DataGap]: ...


class VerificationInputs(Protocol):
    @property
    def offense(self) -> OffenseSnapshot: ...
    @property
    def verdict(self) -> CaseVerdict: ...
    @property
    def confidence(self) -> Confidence: ...
    @property
    def ai_level(self) -> Level: ...
    @property
    def claims(self) -> Sequence[Claim]: ...
    @property
    def critical(self) -> bool: ...


class ReportingInputs(Protocol):
    @property
    def offense(self) -> OffenseSnapshot: ...
    @property
    def enrichment(self) -> EnrichmentContext: ...
    @property
    def verdict(self) -> CaseVerdict: ...
    @property
    def confidence(self) -> Confidence: ...
    @property
    def notify_level(self) -> Level: ...
    @property
    def claims(self) -> Sequence[Claim]: ...
    @property
    def urgent_event_candidates(self) -> Sequence[UrgentEvent]: ...
    @property
    def data_gaps(self) -> Sequence[DataGap]: ...


# --- the tasks --------------------------------------------------------------------------------


def orchestrator_task(
    task: AgentTask, inputs: OrchestratorInputs, skills: SkillRegistry
) -> OrchestratorTask:
    """Triage's decision without its rationale and claims (TriageDecision), the candidates as
    the loaded skills describe them and the plan agents with their manifest budgets."""
    candidates = []
    for candidate in inputs.candidates:
        skill = skills.get(candidate.skill.skill_id, candidate.skill.version)
        if skill is None or skill.content_hash != candidate.skill.content_hash:
            raise RuntimeError(
                f"skill {candidate.skill.skill_id} {candidate.skill.version} is not the one "
                "this worker loaded"
            )
        candidates.append(candidate_skill(skill, agent_role=candidate.agent_id))
    return OrchestratorTask(
        task=task,
        triage=TriageDecision(
            verdict=inputs.verdict,
            confidence=inputs.confidence,
            ai_level=inputs.ai_level,
            needs_investigation=inputs.needs_investigation,
            investigation_focus=tuple(inputs.investigation_focus[:MAX_FOCUS]),
            data_gaps=tuple(inputs.data_gaps),
            injection_suspected=inputs.injection_suspected,
        ),
        offense=inputs.offense,
        candidates=tuple(candidates),
        agents=tuple(
            PlanAgent(agent_id=agent, budgets=_budgets(budget))
            for agent, budget in sorted(inputs.agents.items())
        ),
        plan_budget=inputs.plan_budget,
    )


def investigation_task(
    task: AgentTask,
    inputs: InvestigationInputs,
    evidence: Sequence[EvidenceRef],
    skills: SkillRegistry,
    skill: SkillRef | None,
) -> InvestigationTask:
    """Triage's claims, focus and data gaps without its rationale (InvestigationTriage), the
    claims' evidence and the skill the run's record checked."""
    claims, cited = _fit(
        inputs.claims,
        evidence,
        max_claims=None,
        max_evidence=investigation_limits.MAX_CONTEXT_EVIDENCE,
    )
    return InvestigationTask(
        task=task,
        offense=inputs.offense,
        enrichment=inputs.enrichment,
        triage=InvestigationTriage(
            verdict=inputs.verdict,
            confidence=inputs.confidence,
            ai_level=inputs.ai_level,
            investigation_focus=list(inputs.investigation_focus[:MAX_FOCUS]),
            claims=claims,
            data_gaps=list(inputs.data_gaps),
        ),
        context_evidence=cited,
        skill=None if skill is None else _skill_input(skills, skill),
    )


def verification_task(
    task: AgentTask, inputs: VerificationInputs, evidence: Sequence[EvidenceRef]
) -> VerificationTask:
    """The reviewed decision's enums, its claims with the workflow's critical mark and their
    evidence. A claim without its evidence stays: Verification's code contests it."""
    claims, cited = _fit(
        inputs.claims,
        evidence,
        max_claims=verification_limits.MAX_CLAIMS,
        max_evidence=verification_limits.MAX_EVIDENCE,
        keep_unsupported=True,
    )
    return VerificationTask(
        task=task.model_copy(update={"time_window": _verification_window(task, claims, cited)}),
        reviewed=ReviewedDecision(
            verdict=inputs.verdict, confidence=inputs.confidence, ai_level=inputs.ai_level
        ),
        claims=[ReviewedClaim(claim=claim, critical=inputs.critical) for claim in claims],
        evidence=cited,
        offense=inputs.offense,
    )


def _verification_window(
    task: AgentTask, claims: Sequence[Claim], evidence: Sequence[EvidenceRef]
) -> TimeWindow:
    """Union the claims' evidence windows and clip them to the case's planned window (T-56).

    Storage may no longer have a cited piece of evidence. Without every cited window there is
    no safe narrower range, so Verification keeps the case window.
    """
    cited_ids = {evidence_id for claim in claims for evidence_id in claim.evidence_ids}
    windows = [ref for ref in evidence if ref.evidence_id in cited_ids]
    if not cited_ids or {ref.evidence_id for ref in windows} != cited_ids:
        return task.time_window
    start = max(task.time_window.start, min(ref.time_start for ref in windows))
    end = min(task.time_window.end, max(ref.time_end for ref in windows))
    if start > end:
        return task.time_window
    return TimeWindow(start=start, end=end)


def reporting_task(
    task: AgentTask, inputs: ReportingInputs, evidence: Sequence[EvidenceRef]
) -> ReportingTask:
    """The workflow's decision, the undisputed claims, the urgent event candidates and the
    evidence of both. Candidates come first: they are what the operator looks at."""
    available = {ref.evidence_id for ref in evidence}
    urgent = [event for event in inputs.urgent_event_candidates if event.evidence_id in available][
        : reporting_limits.MAX_URGENT_EVENTS
    ]
    claims, cited = _fit(
        inputs.claims,
        evidence,
        max_claims=reporting_limits.MAX_CLAIMS,
        max_evidence=reporting_limits.MAX_EVIDENCE,
        reserved=[event.evidence_id for event in urgent],
    )
    return ReportingTask(
        task=task,
        decision=CaseDecision(
            verdict=inputs.verdict, confidence=inputs.confidence, notify_level=inputs.notify_level
        ),
        claims=claims,
        evidence=cited,
        urgent_event_candidates=urgent,
        data_gaps=list(inputs.data_gaps[: reporting_limits.MAX_DATA_GAPS]),
        offense=inputs.offense,
        enrichment=inputs.enrichment,
    )


def _fit(
    claims: Iterable[Claim],
    evidence: Sequence[EvidenceRef],
    *,
    max_claims: int | None,
    max_evidence: int,
    reserved: Sequence[str] = (),
    keep_unsupported: bool = False,
) -> tuple[list[Claim], list[EvidenceRef]]:
    """The claims, in order, that fit the limits, and the evidence they and `reserved` cite in
    the order of `evidence`.

    A claim citing evidence that is not in `evidence` is left out unless `keep_unsupported`;
    then it is kept and adds only the evidence that is there.
    """
    available = {ref.evidence_id for ref in evidence}
    chosen = set(reserved) & available
    kept: list[Claim] = []
    for claim in claims:
        if max_claims is not None and len(kept) == max_claims:
            break
        if not keep_unsupported and not set(claim.evidence_ids) <= available:
            continue
        new = (set(claim.evidence_ids) & available) - chosen
        if len(chosen) + len(new) > max_evidence:
            continue
        kept.append(claim)
        chosen |= new
    return kept, [ref for ref in evidence if ref.evidence_id in chosen]


def _skill_input(skills: SkillRegistry, ref: SkillRef) -> SkillInput:
    skill = skills.get(ref.skill_id, ref.version)
    if skill is None or skill.content_hash != ref.content_hash:
        # The record activity checked the skill in this worker; a replay on a worker that loaded
        # other skills must not run the agent with other instructions.
        raise RuntimeError(f"skill {ref.skill_id} {ref.version} is not the one this worker loaded")
    return skill_input(skill)


def _budgets(budget: Budget) -> Budgets:
    return Budgets(
        tokens=budget.tokens, tool_calls=budget.tool_calls, wall_clock_seconds=budget.seconds
    )


# --- the runtimes -----------------------------------------------------------------------------


def _durability() -> TemporalDurability[RunDeps]:
    return TemporalDurability[RunDeps](
        activity_config=TOOL_ACTIVITY, model_activity_config=MODEL_ACTIVITY
    )


def _temporal_activities(agent: Agent[RunDeps, Any]) -> tuple[Callable[..., object], ...]:
    durability = TemporalDurability.from_agent(agent)
    if durability is None:  # pragma: no cover - the builder attached it
        raise RuntimeError("the agent has no TemporalDurability capability")
    return tuple(durability.temporal_activities)


def _check_release(manifest: AgentManifest, model_release: ModelRelease) -> None:
    if model_release.alias != manifest.model_alias:
        raise ValueError(
            f"the model release is for {model_release.alias}, the {manifest.id} agent uses "
            f"{manifest.model_alias}"
        )


@dataclass(frozen=True)
class OrchestratorRuntime:
    agent: OrchestratorAgent
    model_release: ModelRelease
    skills: SkillRegistry
    temporal_activities: tuple[Callable[..., object], ...]

    @classmethod
    def build(
        cls,
        *,
        manifest: AgentManifest,
        prompt: PromptTemplate,
        model: Model,
        model_release: ModelRelease,
        skills: SkillRegistry,
    ) -> Self:
        """Build the durable agent; outside any workflow, before the worker starts."""
        _check_release(manifest, model_release)
        agent = build_orchestrator_agent(
            manifest=manifest, prompt=prompt, model=model, capabilities=[_durability()]
        )
        return cls(
            agent=agent,
            model_release=model_release,
            skills=skills,
            temporal_activities=_temporal_activities(agent.agent),
        )

    async def run(
        self,
        task: AgentTask,
        inputs: OrchestratorInputs,
        *,
        evidence: Sequence[EvidenceRef],
        skill: SkillRef | None,
        run_id: str,
        nonce: str,
    ) -> AgentRun[CasePlan]:
        """One Orchestrator run, in AgentWorkflow's workflow code. It cites no evidence and
        uses no skill."""
        return await self.agent.run(
            orchestrator_task(task, inputs, self.skills),
            run_id=run_id,
            nonce=nonce,
            clock=workflow.time,
        )


@dataclass(frozen=True)
class InvestigationRuntime:
    agent: InvestigationAgent
    model_release: ModelRelease
    skills: SkillRegistry
    temporal_activities: tuple[Callable[..., object], ...]

    @classmethod
    def build(
        cls,
        *,
        manifest: AgentManifest,
        prompt: PromptTemplate,
        profile: ToolsetProfile,
        gateway: GatewayClient,
        model: Model,
        model_release: ModelRelease,
        aql_rules_path: Path,
        skills: SkillRegistry,
    ) -> Self:
        """Build the durable agent with its manifest's gateway profile; outside any workflow."""
        _check_release(manifest, model_release)
        agent = build_investigation_agent(
            manifest=manifest,
            prompt=prompt,
            profiles={profile.name: profile},
            gateway=gateway,
            model=model,
            aql_rules_path=aql_rules_path,
            capabilities=[_durability()],
        )
        return cls(
            agent=agent,
            model_release=model_release,
            skills=skills,
            temporal_activities=_temporal_activities(agent.agent),
        )

    async def run(
        self,
        task: AgentTask,
        inputs: InvestigationInputs,
        *,
        evidence: Sequence[EvidenceRef],
        skill: SkillRef | None,
        run_id: str,
        nonce: str,
    ) -> AgentRun[InvestigationResult]:
        """One Investigation run, in AgentWorkflow's workflow code, with the skill its record
        checked or none."""
        return await self.agent.run(
            investigation_task(task, inputs, evidence, self.skills, skill),
            run_id=run_id,
            nonce=nonce,
            clock=workflow.time,
        )


@dataclass(frozen=True)
class VerificationRuntime:
    agent: VerificationAgent
    model_release: ModelRelease
    temporal_activities: tuple[Callable[..., object], ...]

    @classmethod
    def build(
        cls,
        *,
        manifest: AgentManifest,
        prompt: PromptTemplate,
        profile: ToolsetProfile,
        gateway: GatewayClient,
        model: Model,
        model_release: ModelRelease,
    ) -> Self:
        """Build the durable agent with its manifest's gateway profile; outside any workflow."""
        _check_release(manifest, model_release)
        agent = build_verification_agent(
            manifest=manifest,
            prompt=prompt,
            profiles={profile.name: profile},
            gateway=gateway,
            model=model,
            capabilities=[_durability()],
        )
        return cls(
            agent=agent,
            model_release=model_release,
            temporal_activities=_temporal_activities(agent.agent),
        )

    async def run(
        self,
        task: AgentTask,
        inputs: VerificationInputs,
        *,
        evidence: Sequence[EvidenceRef],
        skill: SkillRef | None,
        run_id: str,
        nonce: str,
    ) -> AgentRun[VerificationResult]:
        """One Verification run, in AgentWorkflow's workflow code; it takes no skill (T-49)."""
        return await self.agent.run(
            verification_task(task, inputs, evidence),
            run_id=run_id,
            nonce=nonce,
            clock=workflow.time,
        )


@dataclass(frozen=True)
class ReportingRuntime:
    agent: ReportingAgent
    model_release: ModelRelease
    temporal_activities: tuple[Callable[..., object], ...]

    @classmethod
    def build(
        cls,
        *,
        manifest: AgentManifest,
        prompt: PromptTemplate,
        model: Model,
        model_release: ModelRelease,
    ) -> Self:
        """Build the durable agent; outside any workflow, before the worker starts."""
        _check_release(manifest, model_release)
        agent = build_reporting_agent(
            manifest=manifest, prompt=prompt, model=model, capabilities=[_durability()]
        )
        return cls(
            agent=agent,
            model_release=model_release,
            temporal_activities=_temporal_activities(agent.agent),
        )

    async def run(
        self,
        task: AgentTask,
        inputs: ReportingInputs,
        *,
        evidence: Sequence[EvidenceRef],
        skill: SkillRef | None,
        run_id: str,
        nonce: str,
    ) -> AgentRun[CaseReport]:
        """One Reporting run, in AgentWorkflow's workflow code; it takes no skill."""
        return await self.agent.run(
            reporting_task(task, inputs, evidence),
            run_id=run_id,
            nonce=nonce,
            clock=workflow.time,
        )


@dataclass(frozen=True, kw_only=True)
class ChainAgent:
    """What the run record of a chain agent needs of it (`ChainActivities`)."""

    manifest: AgentManifest
    prompt_version: str
    toolset_profile: str
    """The manifest's gateway profile; "" for an agent without tools."""
    model_release: ModelRelease
    takes_skill: bool
    """Whether a plan step's skill reaches the agent: only Investigation's prompt has a Skill
    section (decision T-49)."""


@dataclass(frozen=True, kw_only=True)
class ChainRuntime:
    """The four chain agents of the case worker and the skills they use."""

    orchestrator: OrchestratorRuntime
    investigation: InvestigationRuntime
    verification: VerificationRuntime
    reporting: ReportingRuntime
    skills: SkillRegistry
    skills_mode: Mode

    def agents(self) -> dict[str, ChainAgent]:
        """Each agent's record information, by agent ID."""
        orchestrator = self.orchestrator.agent
        investigation = self.investigation.agent
        verification = self.verification.agent
        reporting = self.reporting.agent
        return {
            item.manifest.id: item
            for item in (
                _chain_agent(orchestrator.manifest, orchestrator.prompt, "", self.orchestrator),
                _chain_agent(
                    investigation.manifest,
                    investigation.prompt,
                    investigation.profile.name,
                    self.investigation,
                    takes_skill=True,
                ),
                _chain_agent(
                    verification.manifest,
                    verification.prompt,
                    verification.profile.name,
                    self.verification,
                ),
                _chain_agent(reporting.manifest, reporting.prompt, "", self.reporting),
            )
        }

    @property
    def temporal_activities(self) -> tuple[Callable[..., object], ...]:
        """The four agents' model and tool activities, as TemporalDurability registered them."""
        return (
            *self.orchestrator.temporal_activities,
            *self.investigation.temporal_activities,
            *self.verification.temporal_activities,
            *self.reporting.temporal_activities,
        )


class _HasRelease(Protocol):
    @property
    def model_release(self) -> ModelRelease: ...


def _chain_agent(
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profile: str,
    runtime: _HasRelease,
    *,
    takes_skill: bool = False,
) -> ChainAgent:
    return ChainAgent(
        manifest=manifest,
        prompt_version=prompt.version,
        toolset_profile=profile,
        model_release=runtime.model_release,
        takes_skill=takes_skill,
    )
