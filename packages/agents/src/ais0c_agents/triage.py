"""The Triage agent (architecture §7, §9): the first decision on one QRadar offense.

Input is a TriageTask: the AgentTask, the offense snapshot and the deterministic enrichment.
The model sees the Analysis Catalog as trusted <org_context>; the snapshot and the rest of the
enrichment only inside the `untrusted_*` wrapper. Neither is evidence: claims must cite what the
agent's own tool calls returned. The model returns a TriageOutput, and the run adds the task ID,
status and usage to make the TriageResult.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, AgentRetries, ToolOutput
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ais0c_agents.gateway import GatewayClient
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import PromptTemplate, render_org_context, wrap_json_lines
from ais0c_agents.runner import (
    AgentRun,
    check_cited_evidence,
    prompt_tool_budget,
    run_agent,
    usage_limits,
)
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
OUTPUT_TOOL: Final = "final_result"
# Corrections the model gets for invalid tool calls and for invalid output.
TOOL_RETRIES: Final = 2
OUTPUT_RETRIES: Final = 2
RETRIES: Final[AgentRetries] = {"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES}


# Not a ContractModel: contract models are defined only in packages/contracts.
class TriageTask(BaseModel):
    """Input of the Triage agent; `input_schema: TriageTask` in its manifest."""

    model_config = ConfigDict(extra="forbid")

    task: AgentTask
    offense: OffenseSnapshot
    enrichment: EnrichmentContext


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


@dataclass(frozen=True)
class TriageAgent:
    manifest: AgentManifest
    prompt: PromptTemplate
    profile: ToolsetProfile
    agent: Agent[RunDeps, TriageOutput]

    def render_instructions(self, task: TriageTask, *, nonce: str, tool_budget: int) -> str:
        """The prompt for one run; `nonce` is that run's `untrusted_*` tag suffix."""
        enrichment = task.enrichment.model_dump(mode="json", exclude={"catalog"})
        return self.prompt.render(
            {
                "org_context": render_org_context(task.enrichment.catalog),
                "offense_snapshot": wrap_json_lines(
                    [task.offense.model_dump(mode="json")], source="qradar.offense", nonce=nonce
                ),
                "enrichment": wrap_json_lines(
                    [enrichment], source="platform.enrichment", nonce=nonce
                ),
                "tools": ", ".join(tool.id for tool in self.profile.tools),
                "tool_budget": str(tool_budget),
            }
        )

    async def run(
        self, task: TriageTask, *, nonce: str, clock: Callable[[], float] = time.monotonic
    ) -> AgentRun[TriageResult]:
        """Triage one offense. `nonce` must be fresh for every run (policy.new_nonce())."""
        if task.task.agent_id != self.manifest.id:
            raise ValueError(f"task is for agent {task.task.agent_id!r}, not {self.manifest.id!r}")
        limits = usage_limits(self.manifest, task.task.budget)
        tool_budget = prompt_tool_budget(self.manifest, task.task.budget)
        deps = RunDeps(
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
    agent, its prompt or profile is not the one given, or the profile is unknown.
    """
    if (manifest.input_schema, manifest.output_schema) != (INPUT_SCHEMA, OUTPUT_SCHEMA):
        raise ValueError(
            f"manifest {manifest.id!r} declares {manifest.input_schema} -> "
            f"{manifest.output_schema}, not {INPUT_SCHEMA} -> {OUTPUT_SCHEMA}"
        )
    if prompt.path != manifest.prompt:
        raise ValueError(f"manifest {manifest.id!r} uses {manifest.prompt}, not {prompt.path}")
    profile = profiles.get(manifest.toolset_profile or "")
    if profile is None or profile.name != manifest.toolset_profile:
        raise ValueError(f"unknown toolset profile {manifest.toolset_profile!r}")

    agent = Agent(
        model,
        output_type=ToolOutput(
            TriageOutput, name=OUTPUT_TOOL, description="Return the TriageResult for this offense."
        ),
        deps_type=RunDeps,
        toolsets=[build_gateway_toolset(profile, gateway, agent_id=manifest.id)],
        retries=RETRIES,
        name=manifest.id,
        capabilities=list(capabilities),
    )
    agent.output_validator(check_cited_evidence)
    return TriageAgent(manifest=manifest, prompt=prompt, profile=profile, agent=agent)
