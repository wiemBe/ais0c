"""The Investigation agent: targeted queries, hypotheses, timeline and urgent events.

Investigation receives the structural part of Triage, never its rationale (decision T-45).
Earlier-agent text is untrusted ``agent.*`` data (decision T-48), gateway evidence is shown
under ``ev_c<n>`` aliases (decision T-38), and organization facts stay in ``org_context``.
The agent has only the manifest's gateway profile and can never write to a security product.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from pydantic_ai import Agent, AgentRetries, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.aql import AqlRules, load_aql_rules
from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.evidence import context_alias, render_context_evidence
from ais0c_agents.gateway import GatewayClient
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import (
    KnowledgeItem,
    PromptTemplate,
    render_knowledge,
    render_org_context,
    wrap_json_lines,
)
from ais0c_agents.runner import AgentRun, prompt_tool_budget, run_agent, usage_limits
from ais0c_agents.skills import SkillInput, render_skill
from ais0c_agents.toolset import RunDeps, ToolsetProfile, build_gateway_toolset
from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    EnrichmentContext,
    EvidenceRef,
    InvestigationHypothesis,
    InvestigationResult,
    Level,
    OffenseSnapshot,
    RunStatus,
    ShortText,
    TimelineEntry,
    TimeWindow,
    UrgentEvent,
    Usage,
)

INPUT_SCHEMA: Final = "InvestigationTask"
OUTPUT_SCHEMA: Final = "InvestigationResult"
TOOL_RETRIES: Final = 2
OUTPUT_RETRIES: Final = 2
RETRIES: Final[AgentRetries] = {"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES}
PLACEHOLDERS: Final = frozenset(
    {
        "objective",
        "org_context",
        "offense_snapshot",
        "entity_resolutions",
        "triage_decision",
        "triage_claims",
        "triage_data_gaps",
        "investigation_focus",
        "context_evidence",
        "knowledge",
        "time_window",
        "skill",
        "tools",
        "tool_budget",
    }
)
MAX_CONTEXT_EVIDENCE: Final = 30
MAX_KNOWLEDGE_ITEMS: Final = 10
OFFENSE_SOURCE: Final = "qradar.offense"
ENTITY_RESOLUTION_SOURCE: Final = "qradar.entity_resolution"
CLAIM_SOURCE: Final = "agent.claim"
FOCUS_SOURCE: Final = "agent.focus"
DATA_GAP_SOURCE: Final = "agent.data_gap"
OBJECTIVE_SOURCE: Final = "agent.objective"
NO_ENTITY_RESOLUTION: Final = "No entity resolution is available for this offense."
NO_TRIAGE_CLAIMS: Final = "Triage handed over no claims."
NO_TRIAGE_DATA_GAPS: Final = "Triage reported no data gaps."
NO_INVESTIGATION_FOCUS: Final = "Triage handed over no investigation focus."
NO_CONTEXT_EVIDENCE: Final = "Triage handed over no context evidence."
NO_KNOWLEDGE: Final = "No external knowledge is available for this offense."
RUN_PROMPT: Final = "Investigate the case described in the instructions and return the result."
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
MILLISECOND: Final = timedelta(milliseconds=1)
MAX_WINDOW_PARTS: Final = 6
EXAMPLE_USERNAME: Final = "svc_example"
EXAMPLE_LIMIT: Final = 100


class InvestigationTriage(BaseModel):
    """The structural Triage fields Investigation receives; deliberately no rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    investigation_focus: Annotated[list[ShortText], Field(max_length=5)]
    claims: list[Claim]
    data_gaps: list[DataGap]


class InvestigationTask(BaseModel):
    """Input of the Investigation agent; kept in agents, not the shared contracts package."""

    model_config = ConfigDict(extra="forbid")

    task: AgentTask
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    triage: InvestigationTriage
    context_evidence: Annotated[list[EvidenceRef], Field(max_length=MAX_CONTEXT_EVIDENCE)]
    skill: SkillInput | None = None
    knowledge: Annotated[list[KnowledgeItem], Field(max_length=MAX_KNOWLEDGE_ITEMS)] = []

    @model_validator(mode="after")
    def _claim_evidence_is_present(self) -> "InvestigationTask":
        ids = [ref.evidence_id for ref in self.context_evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("context_evidence contains the same evidence_id more than once")
        available = set(ids)
        missing = {
            evidence_id
            for claim in self.triage.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in available
        }
        if missing:
            raise ValueError("context_evidence must contain the evidence of every Triage claim")
        return self


class InvestigationOutput(BaseModel):
    """The model-owned fields of InvestigationResult."""

    model_config = ConfigDict(extra="forbid", title="InvestigationResult")

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    timeline: Annotated[list[TimelineEntry], Field(max_length=30)]
    hypotheses: Annotated[list[InvestigationHypothesis], Field(max_length=5)]
    urgent_event_candidates: Annotated[list[UrgentEvent], Field(max_length=15)]
    claims: list[Claim]
    data_gaps: list[DataGap]
    injection_suspected: bool


SPEC: Final = AgentSpec(
    name="Investigation",
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    placeholders=PLACEHOLDERS,
    output_type=InvestigationOutput,
    output_description="Return the InvestigationResult for this case.",
    retries=RETRIES,
)


def effective_budget(manifest: AgentManifest, task: InvestigationTask) -> Budget:
    """The smallest of the manifest, plan step and selected skill budgets."""

    token_limits = [manifest.budgets.tokens, task.task.budget.tokens]
    call_limits = [manifest.budgets.tool_calls, task.task.budget.tool_calls]
    second_limits = [manifest.budgets.wall_clock_seconds, task.task.budget.seconds]
    if task.skill is not None:
        token_limits.append(task.skill.budgets.tokens)
        call_limits.append(task.skill.budgets.tool_calls)
        second_limits.append(task.skill.budgets.wall_clock_seconds)
    return Budget(
        tokens=min(token_limits),
        tool_calls=min(call_limits),
        seconds=min(second_limits),
    )


def check_urgent_event_ranks(
    ctx: RunContext[RunDeps], output: InvestigationOutput
) -> InvestigationOutput:
    """Require ranks 1, 2, 3, ... in returned order; retry the model when they are not."""

    del ctx
    ranks = [event.rank for event in output.urgent_event_candidates]
    expected = list(range(1, len(ranks) + 1))
    if ranks != expected:
        raise ModelRetry(
            "urgent_event_candidates must have unique consecutive ranks 1, 2, 3, ... "
            "in the order returned."
        )
    return output


def render_triage_decision(triage: InvestigationTriage) -> str:
    """The three bounded enum values of the decision; no model-authored rationale."""

    return "\n".join(
        (
            f"verdict: {triage.verdict.value}",
            f"confidence: {triage.confidence.value}",
            f"ai_level: {triage.ai_level.value}",
        )
    )


def render_triage_claims(task: InvestigationTask, *, nonce: str) -> str:
    """Triage claims as agent text, citing only context aliases visible to the model."""

    aliases = {
        ref.evidence_id: context_alias(position)
        for position, ref in enumerate(task.context_evidence, start=1)
    }
    blocks = [
        wrap_json_lines(
            [
                {
                    "claim": claim.text,
                    "evidence": [aliases[evidence_id] for evidence_id in claim.evidence_ids],
                }
            ],
            source=CLAIM_SOURCE,
            nonce=nonce,
        )
        for claim in task.triage.claims
    ]
    return "\n\n".join(blocks) or NO_TRIAGE_CLAIMS


@dataclass(frozen=True)
class AqlWindow:
    """One epoch-millisecond AQL time bound a model copies whole (decision T-55)."""

    start_stop: str
    """`START <epoch ms> STOP <epoch ms>`, independent of a console time zone."""
    span: timedelta
    """How far apart START and STOP are."""


def aql_window(
    window: TimeWindow,
) -> AqlWindow:
    """Render a window as one exact epoch-millisecond START/STOP part."""

    start_ms = _epoch_milliseconds(window.start)
    end_ms = _epoch_milliseconds(window.end, round_up=True)
    return AqlWindow(
        start_stop=f"START {start_ms} STOP {end_ms}",
        span=timedelta(milliseconds=end_ms - start_ms),
    )


def example_query(window: AqlWindow, *, limit: int) -> str:
    """An Ariel query that uses `window`'s time bound as-is, for a prompt."""

    # Prompt text built from platform constants and the window's numbers, never from input.
    return (
        "SELECT DATEFORMAT(starttime, 'yyyy-MM-dd HH:mm:ss') AS event_time, username, sourceip, "  # noqa: S608
        f"qid FROM events WHERE username = '{EXAMPLE_USERNAME}' "
        f"LIMIT {limit} {window.start_stop}"
    )


def render_time_window(
    window: TimeWindow,
    *,
    limit: int = EXAMPLE_LIMIT,
    max_span: timedelta | None = None,
) -> str:
    """Render the window in UTC for reading and as copy-ready epoch bounds (decision T-55).

    The bounds are split to the profile limit and capped at MAX_WINDOW_PARTS parts.
    """

    parts, omitted = _aql_windows(window, max_span=max_span)
    lines = [f"Window in UTC, for reading only: {_utc(window.start)} to {_utc(window.end)}"]
    if len(parts) == 1:
        lines.append("Ready AQL time bound (copy this exact complete part after LIMIT):")
    else:
        lines.append("Ready AQL time bounds (copy one exact complete part after LIMIT):")
    lines.extend(
        part.start_stop if len(parts) == 1 else f"{index}. {part.start_stop}"
        for index, part in enumerate(parts, start=1)
    )
    if omitted:
        lines.append(
            f"Only the first {MAX_WINDOW_PARTS} consecutive parts are shown; the rest is omitted."
        )
    lines.append(f"Example: {example_query(parts[0], limit=limit)}")
    return "\n".join(lines)


def _aql_windows(
    window: TimeWindow, *, max_span: timedelta | None
) -> tuple[tuple[AqlWindow, ...], bool]:
    """Return at most six consecutive bounds; say whether later bounds were omitted."""

    if max_span is None or window.end - window.start <= max_span:
        return (aql_window(window),), False
    parts: list[AqlWindow] = []
    start = window.start
    while start < window.end and len(parts) < MAX_WINDOW_PARTS:
        end = min(start + max_span, window.end)
        parts.append(aql_window(TimeWindow(start=start, end=end)))
        start = end
    return tuple(parts), start < window.end


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_milliseconds(moment: datetime, *, round_up: bool = False) -> int:
    """Convert an aware datetime to epoch milliseconds, optionally rounding upward."""

    delta = moment.astimezone(UTC) - EPOCH
    milliseconds = delta // MILLISECOND
    if round_up and EPOCH + milliseconds * MILLISECOND < moment.astimezone(UTC):
        milliseconds += 1
    return milliseconds


def _wrapped_rows(rows: Sequence[JsonValue], *, source: str, nonce: str, empty: str) -> str:
    return wrap_json_lines(rows, source=source, nonce=nonce) if rows else empty


@dataclass(frozen=True)
class InvestigationAgent:
    manifest: AgentManifest
    prompt: PromptTemplate
    profile: ToolsetProfile
    aql_rules: AqlRules
    agent: Agent[RunDeps, InvestigationOutput]

    def render_instructions(self, task: InvestigationTask, *, nonce: str, tool_budget: int) -> str:
        """Render one run without the policy-only floor level or group ID."""

        enrichment = task.enrichment
        resolutions: list[JsonValue] = [
            item.model_dump(mode="json") for item in enrichment.entity_resolutions
        ]
        gaps: list[JsonValue] = [gap.model_dump(mode="json") for gap in task.triage.data_gaps]
        focus: list[JsonValue] = [{"focus": item} for item in task.triage.investigation_focus]
        knowledge = render_knowledge([*enrichment.ioc_hits, *task.knowledge], nonce=nonce)
        return self.prompt.render(
            {
                "objective": wrap_json_lines(
                    [{"objective": task.task.objective}], source=OBJECTIVE_SOURCE, nonce=nonce
                ),
                "org_context": render_org_context(
                    enrichment.catalog, critical_assets=enrichment.critical_asset_hits
                ),
                "offense_snapshot": wrap_json_lines(
                    [task.offense.model_dump(mode="json")], source=OFFENSE_SOURCE, nonce=nonce
                ),
                "entity_resolutions": _wrapped_rows(
                    resolutions,
                    source=ENTITY_RESOLUTION_SOURCE,
                    nonce=nonce,
                    empty=NO_ENTITY_RESOLUTION,
                ),
                "triage_decision": render_triage_decision(task.triage),
                "triage_claims": render_triage_claims(task, nonce=nonce),
                "triage_data_gaps": _wrapped_rows(
                    gaps,
                    source=DATA_GAP_SOURCE,
                    nonce=nonce,
                    empty=NO_TRIAGE_DATA_GAPS,
                ),
                "investigation_focus": _wrapped_rows(
                    focus,
                    source=FOCUS_SOURCE,
                    nonce=nonce,
                    empty=NO_INVESTIGATION_FOCUS,
                ),
                "context_evidence": (
                    render_context_evidence(task.context_evidence, nonce=nonce)
                    or NO_CONTEXT_EVIDENCE
                ),
                "knowledge": knowledge or NO_KNOWLEDGE,
                "time_window": render_time_window(
                    task.task.time_window, max_span=self.aql_rules.aql.max_window
                ),
                "skill": render_skill(task.skill),
                "tools": ", ".join(tool.id for tool in self.profile.tools),
                "tool_budget": str(tool_budget),
            }
        )

    async def run(
        self,
        task: InvestigationTask,
        *,
        run_id: str,
        nonce: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> AgentRun[InvestigationResult]:
        """Run one investigation; every ToolIntent carries ``run_id``."""

        if task.task.agent_id != self.manifest.id:
            raise ValueError(f"task is for agent {task.task.agent_id!r}, not {self.manifest.id!r}")
        if task.skill is not None and self.manifest.id not in task.skill.allowed_agent_roles:
            raise ValueError(
                f"skill {task.skill.ref.skill_id!r} does not allow agent role {self.manifest.id!r}"
            )
        budget = effective_budget(self.manifest, task)
        deps = RunDeps(
            run_id=run_id,
            case_id=task.task.case_id,
            hunt_id=task.task.hunt_id,
            time_window=task.task.time_window,
            nonce=nonce,
            context_evidence=tuple(ref.evidence_id for ref in task.context_evidence),
        )

        def finalize(output: InvestigationOutput, usage: Usage) -> InvestigationResult:
            return InvestigationResult.model_validate(
                {
                    **output.model_dump(),
                    "task_id": task.task.task_id,
                    "status": RunStatus.COMPLETED,
                    "usage": usage,
                }
            )

        return await run_agent(
            self.agent,
            user_prompt=RUN_PROMPT,
            instructions=self.render_instructions(
                task,
                nonce=nonce,
                tool_budget=prompt_tool_budget(self.manifest, budget),
            ),
            deps=deps,
            limits=usage_limits(self.manifest, budget),
            prompt=self.prompt,
            finalize=finalize,
            clock=clock,
        )


def build_investigation_agent(
    *,
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profiles: Mapping[str, ToolsetProfile],
    gateway: GatewayClient,
    model: Model,
    aql_rules_path: Path,
    capabilities: Sequence[AbstractCapability[RunDeps]] = (),
) -> InvestigationAgent:
    """Build the agent once, outside workflows, with only its manifest profile."""

    profile = check_agent_config(SPEC, manifest, prompt, profiles)
    aql_rules = load_aql_rules(aql_rules_path, profile=profile.name)
    agent = create_agent(
        SPEC,
        manifest=manifest,
        model=model,
        toolsets=[build_gateway_toolset(profile, gateway, agent_id=manifest.id)],
        aql=aql_rules,
        capabilities=capabilities,
    )
    agent.output_validator(check_urgent_event_ranks)
    return InvestigationAgent(
        manifest=manifest,
        prompt=prompt,
        profile=profile,
        aql_rules=aql_rules,
        agent=agent,
    )
