"""What every agent's builder does: check its configuration and create the Pydantic AI agent.

An agent module declares an AgentSpec: its input and output schemas, its prompt's inputs and
its output type. check_agent_config checks the manifest and the prompt against it and returns
the manifest's toolset profile; create_agent creates the agent with the output validators every
agent has. Build an agent once, outside any workflow (TemporalDurability requires it).

An agent with tools passes its profiles and gets the manifest's profile back. An agent without
tools (Orchestrator, Reporting) passes none: its manifest has `toolset_profile: null` and may
have a tool call budget of 0, and run_agent runs it like any other agent.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, overload

from pydantic import BaseModel
from pydantic_ai import Agent, AgentRetries, ToolOutput
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset

from ais0c_agents.aql import AqlRules, SuggestedAqlCheck
from ais0c_agents.evidence import check_evidence, check_evidence_fields
from ais0c_agents.manifest import AgentManifest
from ais0c_agents.prompts import SHARED_RULES_PLACEHOLDER, PromptTemplate
from ais0c_agents.toolset import RunDeps, ToolsetProfile

OUTPUT_TOOL: Final = "final_result"


@dataclass(frozen=True, kw_only=True)
class AgentSpec[OutputT: BaseModel]:
    """What an agent's code fixes about it; its manifest and prompt must agree."""

    name: str
    """For error messages, e.g. "Triage"."""
    input_schema: str
    output_schema: str
    placeholders: frozenset[str]
    """The template's inputs besides the shared rules."""
    output_type: type[OutputT]
    """What the model returns. It names itself `output_schema` to the model (model_config
    title) and has no docstring: Pydantic AI would add it to the output tool's description."""
    output_description: str
    retries: AgentRetries
    """Corrections the model gets for invalid tool calls and for invalid output."""


@overload
def check_agent_config(
    spec: AgentSpec[BaseModel],
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profiles: Mapping[str, ToolsetProfile],
) -> ToolsetProfile: ...


@overload
def check_agent_config(
    spec: AgentSpec[BaseModel],
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profiles: None = None,
) -> None: ...


def check_agent_config(
    spec: AgentSpec[BaseModel],
    manifest: AgentManifest,
    prompt: PromptTemplate,
    profiles: Mapping[str, ToolsetProfile] | None = None,
) -> ToolsetProfile | None:
    """Check that `manifest` describes this agent and `prompt` is its prompt.

    With `profiles`, the agent has tools: returns the manifest's profile. Without, it has none
    and its manifest must not name a profile. Raises ValueError when the manifest declares other
    schemas, names another prompt or shared rules version, or a profile that is unknown, filed
    under another name or not expected; or when the prompt does not take the agent's inputs.
    """
    if (manifest.input_schema, manifest.output_schema) != (spec.input_schema, spec.output_schema):
        raise ValueError(
            f"manifest {manifest.id!r} declares {manifest.input_schema} -> "
            f"{manifest.output_schema}, not {spec.input_schema} -> {spec.output_schema}"
        )
    if prompt.path != manifest.prompt:
        raise ValueError(f"manifest {manifest.id!r} uses {manifest.prompt}, not {prompt.path}")
    if prompt.shared_rules_path != manifest.shared_rules:
        raise ValueError(
            f"manifest {manifest.id!r} uses {manifest.shared_rules}, not {prompt.shared_rules_path}"
        )
    if (inputs := prompt.placeholders - {SHARED_RULES_PLACEHOLDER}) != spec.placeholders:
        raise ValueError(
            f"{prompt.path} takes {', '.join(sorted(inputs))}; the {spec.name} agent fills "
            f"{', '.join(sorted(spec.placeholders))}"
        )
    if profiles is None:
        if manifest.toolset_profile is not None:
            raise ValueError(
                f"manifest {manifest.id!r} names toolset profile {manifest.toolset_profile!r}; "
                f"the {spec.name} agent has no tools"
            )
        return None
    profile = profiles.get(manifest.toolset_profile or "")
    if profile is None or profile.name != manifest.toolset_profile:
        raise ValueError(f"unknown toolset profile {manifest.toolset_profile!r}")
    return profile


def create_agent[OutputT: BaseModel](
    spec: AgentSpec[OutputT],
    *,
    manifest: AgentManifest,
    model: Model,
    toolsets: Sequence[AbstractToolset[RunDeps]],
    aql: AqlRules | None,
    capabilities: Sequence[AbstractCapability[RunDeps]] = (),
) -> Agent[RunDeps, OutputT]:
    """The Pydantic AI agent, with the output validators every agent has.

    check_evidence checks every evidence field of the output (decisions T-27, T-38). With
    `aql`, SuggestedAqlCheck checks the output's urgent event queries against those rules
    (decision T-39); an agent whose output can carry a query passes them, and one that only
    copies checked queries passes None. `capabilities` are attached here, the only time
    Pydantic AI binds them; a workflow passes TemporalDurability. Raises TypeError when the
    output type holds an evidence ID that check_evidence cannot check.
    """
    check_evidence_fields(spec.output_type)
    agent = Agent(
        model,
        output_type=ToolOutput(
            spec.output_type, name=OUTPUT_TOOL, description=spec.output_description
        ),
        deps_type=RunDeps,
        toolsets=list(toolsets),
        retries=spec.retries,
        name=manifest.id,
        capabilities=list(capabilities),
    )
    agent.output_validator(check_evidence)
    if aql is not None:
        agent.output_validator(SuggestedAqlCheck(aql))
    return agent
