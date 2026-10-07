"""The Triage agent (architecture §7, §9): the first decision on one QRadar offense.

Input is a TriageTask: the AgentTask, the offense snapshot, the deterministic enrichment and
external knowledge; in a group case also the group's summary, with the snapshot of one of its
offenses (T-027). Each part reaches the model in its trust layer (architecture §22, T-20):
the Analysis Catalog entries and critical asset hits as organization facts in <org_context>;
the snapshot, the group summary, the entity resolutions, the IOC hits and other knowledge only
inside the `untrusted_*` wrapper. The floor level is policy, which code applies, so the model never sees
it. None of the context is evidence: claims must cite what the agent's own tool calls returned.
The model returns a TriageOutput, and the run adds the task ID, status and usage to make the
TriageResult.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import Agent, AgentRetries
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.gateway import GatewayClient
from ais0c_agents.group import GROUP_SUMMARY_SOURCE, GroupSummary
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import (
    KnowledgeItem,
    PromptTemplate,
    render_knowledge,
    render_org_context,
    wrap_json_lines,
)
from ais0c_agents.runner import AgentRun, prompt_tool_budget, run_agent, usage_limits
from ais0c_agents.toolset import RunDeps, ToolsetProfile, build_gateway_toolset
from ais0c_contracts import (
    AgentTask,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    EnrichmentContext,
    Level,
    OffenseSnapshot,
    RunStatus,
    ShortText,
    Summary,
    TriageResult,
    Usage,
)
from ais0c_policy import neutralize_tags

INPUT_SCHEMA: Final = "TriageTask"
OUTPUT_SCHEMA: Final = "TriageResult"
# Corrections the model gets for invalid tool calls and for invalid output.
TOOL_RETRIES: Final = 2
OUTPUT_RETRIES: Final = 2
RETRIES: Final[AgentRetries] = {"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES}
# The template's inputs besides the shared rules (prompts/triage/v3.md).
PLACEHOLDERS: Final = frozenset(
    {"org_context", "offense_snapshot", "entity_resolutions", "knowledge", "tools", "tool_budget"}
)
OFFENSE_SOURCE: Final = "qradar.offense"
# Entity resolution answers from QRadar's DHCP, VPN and logon events and asset model (§16).
ENTITY_RESOLUTION_SOURCE: Final = "qradar.entity_resolution"
NO_ENTITY_RESOLUTION: Final = "No entity resolution is available for this offense."
NO_KNOWLEDGE: Final = "No external knowledge is available for this offense."
MAX_KNOWLEDGE_ITEMS: Final = 10


# Not a ContractModel: contract models are defined only in packages/contracts.
class TriageTask(BaseModel):
    """Input of the Triage agent; `input_schema: TriageTask` in its manifest."""

    model_config = ConfigDict(extra="forbid")

    task: AgentTask
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    knowledge: Annotated[list[KnowledgeItem], Field(max_length=MAX_KNOWLEDGE_ITEMS)] = []
    """External knowledge about the offense: ATT&CK, CTI, runbooks, past cases. Empty until
    the knowledge plane supplies it (Faz 3); the enrichment's IOC hits come on their own."""
    group_summary: GroupSummary | None = None
    """Set in a group case: the summary of the group's offenses, of which `offense` is one
    (T-027). It follows the snapshot in its own untrusted block."""


# What the model returns: a TriageResult without task_id, status and usage, which the run fills
# in. The model sees this schema under the name TriageResult, as its prompt says. No docstring:
# Pydantic AI would add it to the output tool's description.
class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", title="TriageResult")

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    rationale: Summary
    needs_investigation: bool
    investigation_focus: Annotated[list[ShortText], Field(max_length=5)]
    claims: list[Claim]
    data_gaps: list[DataGap]
    injection_suspected: bool


SPEC: Final = AgentSpec(
    name="Triage",
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    placeholders=PLACEHOLDERS,
    output_type=TriageOutput,
    output_description="Return the TriageResult for this offense.",
    retries=RETRIES,
)


@dataclass(frozen=True)
class TriageAgent:
    manifest: AgentManifest
    prompt: PromptTemplate
    profile: ToolsetProfile
    agent: Agent[RunDeps, TriageOutput]

    def render_instructions(self, task: TriageTask, *, nonce: str, tool_budget: int) -> str:
        """The prompt for one run; `nonce` is that run's `untrusted_*` tag suffix.

        The enrichment's `floor_level` and `group_id` stay out: code applies the floor, and
        the group is the platform's own bookkeeping.
        """
        enrichment = task.enrichment
        resolutions: list[JsonValue] = [
            resolution.model_dump(mode="json") for resolution in enrichment.entity_resolutions
        ]
        knowledge = render_knowledge([*enrichment.ioc_hits, *task.knowledge], nonce=nonce)
        return self.prompt.render(
            {
                "org_context": render_org_context(
                    enrichment.catalog, critical_assets=enrichment.critical_asset_hits
                ),
                "offense_snapshot": _offense_snapshot(task, nonce=nonce),
                "entity_resolutions": (
                    wrap_json_lines(resolutions, source=ENTITY_RESOLUTION_SOURCE, nonce=nonce)
                    if resolutions
                    else NO_ENTITY_RESOLUTION
                ),
                "knowledge": knowledge or NO_KNOWLEDGE,
                "tools": ", ".join(tool.id for tool in self.profile.tools),
                "tool_budget": str(tool_budget),
            }
        )

    async def run(
        self,
        task: TriageTask,
        *,
        run_id: str,
        nonce: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> AgentRun[TriageResult]:
        """Triage one offense as the agent run `run_id` (`agent_runs.run_id`).

        Every tool call carries `run_id`, so the gateway records it under the run. `nonce` must
        be fresh for every run (policy.new_nonce()). Raises ValueError when the task is for
        another agent or `run_id` is empty or too long.
        """
        if task.task.agent_id != self.manifest.id:
            raise ValueError(f"task is for agent {task.task.agent_id!r}, not {self.manifest.id!r}")
        limits = usage_limits(self.manifest, task.task.budget)
        tool_budget = prompt_tool_budget(self.manifest, task.task.budget)
        deps = RunDeps(
            run_id=run_id,
            case_id=task.task.case_id,
            hunt_id=task.task.hunt_id,
            time_window=task.task.time_window,
            nonce=nonce,
        )

        def finalize(output: TriageOutput, usage: Usage) -> TriageResult:
            return TriageResult.model_validate(
                {
                    **output.model_dump(),
                    "task_id": task.task.task_id,
                    "status": RunStatus.COMPLETED,
                    "usage": usage,
                }
            )

        return await run_agent(
            self.agent,
            user_prompt=neutralize_tags(task.task.objective),
            instructions=self.render_instructions(task, nonce=nonce, tool_budget=tool_budget),
            deps=deps,
            limits=limits,
            prompt=self.prompt,
            finalize=finalize,
            clock=clock,
        )


def _offense_snapshot(task: TriageTask, *, nonce: str) -> str:
    """The offense's untrusted block; in a group case the group summary's block after it."""
    snapshot = wrap_json_lines(
        [task.offense.model_dump(mode="json")], source=OFFENSE_SOURCE, nonce=nonce
    )
    if task.group_summary is None:
        return snapshot
    summary = wrap_json_lines(
        [task.group_summary.model_dump(mode="json")], source=GROUP_SUMMARY_SOURCE, nonce=nonce
    )
    return f"{snapshot}\n\n{summary}"


def build_triage_agent(
    *,
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profiles: Mapping[str, ToolsetProfile],
    gateway: GatewayClient,
    model: Model,
    capabilities: Sequence[AbstractCapability[RunDeps]] = (),
) -> TriageAgent:
    """Build the agent once, outside any workflow (TemporalDurability requires it).

    The agent gets only the tools of its manifest's profile. `capabilities` are attached when
    the agent is built, the only time Pydantic AI binds them; a workflow passes
    TemporalDurability here. Raises ValueError when the manifest does not describe a triage
    agent, its prompt, shared rules or profile is not the one given, the prompt does not take
    this agent's inputs, or the profile is unknown.
    """
    profile = check_agent_config(SPEC, manifest, prompt, profiles)
    agent = create_agent(
        SPEC,
        manifest=manifest,
        model=model,
        toolsets=[build_gateway_toolset(profile, gateway, agent_id=manifest.id)],
        aql=None,
        capabilities=capabilities,
    )
    return TriageAgent(manifest=manifest, prompt=prompt, profile=profile, agent=agent)
