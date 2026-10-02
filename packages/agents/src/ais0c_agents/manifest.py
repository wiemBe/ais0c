"""Agent manifests (architecture §8.1): config/agents/<agent>.yaml.

A manifest is checked against the model registry (§8.4) before the agent may start: an unknown
model alias or a capability the model lacks stops the agent with an error (fail closed).
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ais0c_agents._yaml import load_yaml
from ais0c_agents.llm import ModelAlias
from ais0c_agents.registry import Capability, ModelRegistry, ModelRegistryEntry

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
SchemaName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Za-z0-9]{0,99}$")]
# prompts/<agent>/v<N>.md (docs/impl/prompts.md); never a path outside prompts/.
PromptPath = Annotated[
    str, StringConstraints(pattern=r"^prompts/[a-z][a-z0-9-]*/v[1-9][0-9]*\.md$")
]
SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){2}$")]
WorkflowType = Literal["case", "hunt", "tuning"]


class ManifestError(ValueError):
    """The manifest is invalid, or its model does not exist or lacks a required capability."""


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tokens: Annotated[int, Field(gt=0)]
    tool_calls: Annotated[int, Field(gt=0)]
    wall_clock_seconds: Annotated[int, Field(gt=0)]


class AgentManifest(BaseModel):
    """The fields of architecture §8.1. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Name
    version: SemVer
    role: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    workflow_types: Annotated[frozenset[WorkflowType], Field(min_length=1)]
    model_alias: ModelAlias
    required_model_capabilities: Annotated[frozenset[Capability], Field(min_length=1)]
    # Pydantic model names; each agent's builder checks they are its own input and output.
    input_schema: SchemaName
    output_schema: SchemaName
    # null for agents without tools (Orchestrator, Reporting).
    toolset_profile: Name | None
    # Model requests per run, output retries included.
    max_steps: Annotated[int, Field(gt=0)]
    budgets: Budgets
    # The autonomy ceiling is L0 (D-02). L1 (templated user e-mail) is a Faz 4 option.
    autonomy: Literal["L0"]
    can_delegate: bool
    prompt: PromptPath
    eval_suites: Annotated[frozenset[Name], Field(min_length=1)]


def parse_manifest(data: object, registry: ModelRegistry) -> AgentManifest:
    """Validate manifest data and check its model against the registry."""
    try:
        manifest = AgentManifest.model_validate(data)
    except ValidationError as error:
        raise ManifestError(f"invalid agent manifest: {error}") from error
    check_model(manifest, registry)
    return manifest


def load_manifest(path: Path, registry: ModelRegistry) -> AgentManifest:
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as error:
        raise ManifestError(f"cannot read agent manifest {path}: {error}") from error
    try:
        return parse_manifest(data, registry)
    except ManifestError as error:
        raise ManifestError(f"{path}: {error}") from error


def check_model(manifest: AgentManifest, registry: ModelRegistry) -> ModelRegistryEntry:
    """Return the registry entry of the manifest's model alias.

    Raises ManifestError if the alias is not in the registry or lacks a required capability.
    """
    entry = registry.get(manifest.model_alias)
    if entry is None:
        raise ManifestError(
            f"agent {manifest.id!r}: model alias {manifest.model_alias!r} is not in the model registry"
        )
    missing = manifest.required_model_capabilities - entry.capabilities
    if missing:
        raise ManifestError(
            f"agent {manifest.id!r}: model alias {manifest.model_alias!r} lacks the required "
            f"capabilities {', '.join(sorted(missing))}"
        )
    return entry
